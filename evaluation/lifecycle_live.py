"""One owner-authorized embedding-only lifecycle acceptance; no resume/retry mode."""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import url2pathname

import httpx
from evaluation.lifecycle_offline import verify_preservation
from support_agent.config import Settings
from support_agent.documents import local_path, read_document
from support_agent.embeddings import ManagedOllama, PINNED_DIGEST, PINNED_MODEL
from support_agent.integrity import checked_index
from support_agent.lifecycle import apply, preview, status
from support_agent.ollama import unit
from support_agent.store import connect, ingest

ROOT=Path(__file__).resolve().parents[1]
BASELINE="9c3bdb0879b247969a9bc67cda044ad095b97a07"
RUN_ID="lifecycle-real-once"
STAGES=("initial","add","content-update","metadata-update","no-op-reindex","delete")
COUNTS={"initial":2,"add":1,"content-update":1}
ALLOWED_NEW={"evaluation/lifecycle_live.py","tests/test_lifecycle_live.py","docs/lifecycle-live-acceptance.md"}


class StopRun(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def encoded(value):
    return (json.dumps(value,indent=2,ensure_ascii=False)+"\n").encode("utf-8")


def save_new(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("xb") as stream:
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())


class Journal:
    def __init__(self,path):
        self.path=path
        with path.open("xb"): pass

    def write(self,event):
        with self.path.open("ab") as stream:
            stream.write((json.dumps({"utc":utc(),**event},ensure_ascii=False)+"\n").encode())
            stream.flush(); os.fsync(stream.fileno())


class Budget:
    def __init__(self,clock=time.monotonic):
        self.clock=clock; self.started=clock()
        self.counts={"embedding_posts":0,"metadata_gets":0}
        self.stage=None; self.stages=[]; self.slot=0

    def remaining(self):
        result=600-(self.clock()-self.started)
        if result<=0: raise StopRun("overall_timeout")
        return result

    def begin(self,stage):
        self.remaining()
        if len(self.stages)>=len(STAGES) or stage!=STAGES[len(self.stages)]:
            raise StopRun("repeated_or_unexpected_operation")
        if self.stage: self.finish()
        self.stage=stage; self.stages.append(stage); self.slot=0

    def finish(self):
        self.remaining()
        if self.slot != (3 if self.stage in COUNTS else 0):
            raise StopRun("uncertain_or_incomplete_request_budget")

    def reserve(self,request):
        self.remaining()
        if str(request.url.copy_with(path="/",query=None))!="http://127.0.0.1:11434/" or request.url.query:
            raise StopRun("unauthorized_origin_or_query")
        if self.stage not in COUNTS or self.slot>=3:
            raise StopRun("request_outside_authorized_stage_or_retry")
        expected=("GET","/api/tags") if self.slot in {0,2} else ("POST","/api/embed")
        if (request.method,request.url.path)!=expected:
            raise StopRun("unauthorized_endpoint_method_or_order")
        if request.method=="POST":
            try: payload=json.loads(request.content)
            except (ValueError,TypeError): raise StopRun("invalid_embedding_request")
            if not isinstance(payload,dict) or set(payload)!={"model","input","truncate","keep_alive","options"} or payload["model"]!=PINNED_MODEL or payload["truncate"] is not False or payload["keep_alive"]!="5m" or payload["options"]!={"num_ctx":2048}:
                raise StopRun("embedding_request_configuration_mismatch")
            inputs=payload["input"]
            if not isinstance(inputs,list) or len(inputs)!=COUNTS[self.stage] or any(not isinstance(t,str) or not t.startswith("search_document: ") or len(t)>1200 for t in inputs):
                raise StopRun("query_embedding_or_batch_violation")
            kind,cap="embedding_posts",3
        else:
            if request.content: raise StopRun("unexpected_metadata_payload")
            kind,cap="metadata_gets",6
        if self.counts[kind]>=cap: raise StopRun("budget_exceeded")
        self.counts[kind]+=1; self.slot+=1
        return kind,sum(self.counts.values())


class RecordedTransport(httpx.AsyncBaseTransport):
    def __init__(self,budget,journal,output,inner):
        self.budget,self.journal,self.output,self.inner=budget,journal,output,inner

    async def handle_async_request(self,request):
        kind,sequence=self.budget.reserve(request)
        payload=json.loads(request.content) if request.content else None
        record={"sequence":sequence,"stage":self.budget.stage,"kind":kind,"method":request.method,"endpoint":request.url.path,
                "counters":dict(self.budget.counts),"request_sha256":hashlib.sha256(request.content).hexdigest()}
        save_new(self.output/"requests"/f"{sequence:03d}.json",{"method":request.method,"url":str(request.url),"payload":payload})
        self.journal.write({"event":"request_attempt",**record}) # durable BEFORE any socket operation
        started=time.monotonic()
        try:
            async with asyncio.timeout(min(90,self.budget.remaining())):
                response=await self.inner.handle_async_request(request)
                try:
                    raw=bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw)>2_000_000: raise StopRun("response_size_limit")
                finally: await response.aclose()
            record["status_code"]=response.status_code
            # Save failed/malformed responses too; no retry path exists.
            response_path=self.output/"responses"/f"{sequence:03d}.body"
            with response_path.open("xb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            record["response_sha256"]=sha(response_path)
            if response.status_code!=200: raise StopRun("failed_model_response")
            try: body=json.loads(raw)
            except ValueError as exc: raise StopRun("invalid_model_json") from exc
            if not isinstance(body,dict): raise StopRun("invalid_model_response")
            if kind=="metadata_gets":
                models=body.get("models")
                if not isinstance(models,list) or any(not isinstance(m,dict) for m in models): raise StopRun("invalid_identity_response")
                matches=[m for m in models if m.get("name")==PINNED_MODEL]
                if len(matches)!=1 or matches[0].get("digest")!=PINNED_DIGEST or matches[0].get("remote_host"):
                    raise StopRun("model_identity_mismatch")
                record["verified_identity"]=matches[0]
            else:
                vectors=body.get("embeddings")
                if body.get("model")!=PINNED_MODEL or not isinstance(vectors,list) or len(vectors)!=COUNTS[self.budget.stage]: raise StopRun("failed_embedding_response")
                if any(len(unit(v))!=768 for v in vectors): raise StopRun("embedding_dimension_mismatch")
                record.update(model=body["model"],embedding_count=len(vectors),dimension=768)
            self.budget.remaining()
            record["outcome"]="verified_response"
            return httpx.Response(response.status_code,content=bytes(raw),request=request)
        except BaseException as exc:
            record.update(outcome="failed",error_type=type(exc).__name__,error=str(exc))
            raise
        finally:
            record["seconds"]=round(time.monotonic()-started,6)
            self.journal.write({"event":"request_finished",**record})

    async def aclose(self):
        await self.inner.aclose()


@contextmanager
def guarded_access(database,budget,journal,output,transport_factory=None):
    if os.environ.get("PYTEST_CURRENT_TEST") and transport_factory is None:
        raise StopRun("offline_tests_require_an_explicit_fake_transport")
    original_connect=sqlite3.connect
    original_client=httpx.AsyncClient
    def db_connect(value,*args,**kwargs):
        target=Path(url2pathname(urlsplit(str(value)).path)) if str(value).startswith("file:") else Path(value)
        if target.resolve()!=database.resolve(): raise StopRun("unexpected_database_access:"+str(value))
        budget.remaining()
        journal.write({"event":"database_open","stage":budget.stage,"database":str(target.resolve()),"readonly":bool(kwargs.get("uri") and "mode=ro" in str(value))})
        return original_connect(value,*args,**kwargs)
    def client(**kwargs):
        if kwargs.get("trust_env") is not False or kwargs.get("follow_redirects") is not False or "transport" in kwargs:
            raise StopRun("unsafe_http_client_configuration")
        inner=transport_factory() if transport_factory else httpx.AsyncHTTPTransport(retries=0)
        return original_client(transport=RecordedTransport(budget,journal,output,inner),**kwargs)
    # Covers the unchanged production adapter and all lifecycle SQLite opens.
    with patch("sqlite3.connect",db_connect),patch("httpx.AsyncClient",client):
        yield


def git(*args):
    return subprocess.check_output(["git","-c","safe.directory="+ROOT.as_posix(),*args],cwd=ROOT,timeout=10)


def preflight():
    if git("status","--porcelain").strip(): raise StopRun("working_tree_not_clean")
    head=git("rev-parse","HEAD").decode().strip()
    git("merge-base","--is-ancestor",BASELINE,head)
    changed=set(git("diff","--name-only",BASELINE,head).decode().splitlines())
    if changed-ALLOWED_NEW: raise StopRun("approved_source_or_historical_artifact_changed")
    local_path(ROOT/".local")
    if any(p.exists() or p.is_symlink() for p in (ROOT/".local"/RUN_ID,ROOT/".local"/(RUN_ID+".claim.json"),ROOT/"reports"/RUN_ID)):
        raise StopRun("run_already_claimed_or_paths_exist")
    verify_preservation(require_local=True)
    proof_path=ROOT/".local/lifecycle-live-offline-proof.json"
    proof=json.loads(proof_path.read_text(encoding="utf-8"))
    if proof.get("commit")!=head or proof.get("exit_code")!=0 or proof.get("runner_sha256")!=sha(Path(__file__)) or proof.get("tests_sha256")!=sha(ROOT/"tests/test_lifecycle_live.py") or proof.get("log_sha256")!=sha(ROOT/".local/lifecycle-live-offline-tests.log"):
        raise StopRun("offline_preflight_proof_missing_or_stale")
    names=set(git("ls-tree","-r","--name-only",BASELINE).decode().splitlines())
    names.update(json.loads((ROOT/"evaluation/relevance/preservation.json").read_text())["files"])
    artifacts={n:sha(ROOT/n) for n in sorted(names)}
    return {"approved_source_commit":BASELINE,"runner_commit":head,"offline_proof":proof,"original_artifacts":artifacts}


def preserve(artifacts):
    for name,expected in artifacts.items():
        if sha(ROOT/name)!=expected: raise StopRun("historical_preservation_failure:"+name)


def inspect_index(settings,sources,active_ids):
    before=sha(settings.db_path)
    with connect(settings.db_path) as db:
        rows,dimension=checked_index(db,settings)
        if dimension!=768: raise StopRun("stored_dimension_mismatch")
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok": raise StopRun("sqlite_integrity_failure")
        meta=dict(db.execute("SELECT key,value FROM metadata"))
        if meta.get("embed_digest")!=PINNED_DIGEST: raise StopRun("stored_identity_mismatch")
        docs=[dict(r) for r in db.execute("SELECT * FROM documents ORDER BY id")]
        raw=[dict(r) for r in db.execute("SELECT * FROM chunks ORDER BY id")]
        if {d["id"] for d in docs}!=set(active_ids) or len(raw)!=len(active_ids): raise StopRun("committed_membership_failure")
        for d in docs:
            source,pieces=read_document(sources,d["filename"],settings)
            if len(pieces)!=1 or tuple(d.values())!=source: raise StopRun("committed_source_mismatch")
            if [r[0] for r in db.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH ?",('"'+d["id"]+'"',))]!=[d["id"]+":c000"]:
                raise StopRun("fts_membership_failure")
        if "wren" not in active_ids and db.execute("SELECT chunk_id FROM chunks_fts WHERE chunks_fts MATCH 'wren'").fetchone(): raise StopRun("deleted_document_in_fts")
    # Verify inverted-index correspondence without committing verification writes.
    with connect(settings.db_path,readonly=False) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('integrity-check')")
        if db.execute("PRAGMA foreign_key_check").fetchone(): raise StopRun("foreign_key_failure")
        db.rollback()
    if sha(settings.db_path)!=before: raise StopRun("verification_changed_index")
    return {"index_sha256":before,"dimension":dimension,"metadata":meta,"documents":docs,
            "chunks":[{k:v for k,v in r.items() if k!="embedding"} | {"embedding_sha256":hashlib.sha256(r["embedding"].encode()).hexdigest()} for r in raw],
            "source_offsets_verified":True,"fts5_verified":True,"sqlite_integrity_verified":True,
            "semantic_candidate_ids":[r["id"] for r in rows]}


def run(provenance,budget,*,transport_factory=None,hard_deadline=True):
    workspace=ROOT/".local"/RUN_ID
    output=ROOT/"reports"/RUN_ID
    claim=ROOT/".local"/(RUN_ID+".claim.json")
    save_new(claim,{"run_id":RUN_ID,"consumed":True,"claimed_at_utc":utc(),"approved_source_commit":BASELINE,
                    "runner_commit":provenance["runner_commit"],"embedding_model":PINNED_MODEL,"digest":PINNED_DIGEST,
                    "limits":{"embedding_posts":3,"metadata_gets":6,"generation":0,"query_embeddings":0,"seconds":600},
                    "database":str(workspace/"fictional.sqlite3"),"rule":"Never remove, reuse or resume this claim"})
    workspace.mkdir(); output.mkdir()
    with (output/".gitattributes").open("xb") as stream:
        stream.write(b"*.body -text\n"); stream.flush(); os.fsync(stream.fileno())
    sources=workspace/"fictional-sources"; sources.mkdir()
    (output/"responses").mkdir()
    journal=Journal(output/"requests.jsonl")
    save_new(output/"run-claim.json",json.loads(claim.read_text()))
    save_new(output/"preflight.json",provenance)
    settings=replace(Settings(),db_path=workspace/"fictional.sqlite3",managed_embeddings=True)
    adapter=ManagedOllama(settings) # no model access in constructor
    report={"run_id":RUN_ID,"kind":"single actual local embedding-only fictional lifecycle acceptance",
            "approved_source_commit":BASELINE,"runner_commit":provenance["runner_commit"],"status":"running",
            "model":PINNED_MODEL,"required_digest":PINNED_DIGEST,"operations":[],"request_counts":dict(budget.counts),
            "query_embeddings":0,"generation_posts":0,"retries":0,"downloads":0,"substitutions":0,"started_at_utc":utc()}
    def checkpoint():
        temp=output/"checkpoint.tmp"
        with temp.open("wb") as stream:
            stream.write(encoded(report)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp,output/"checkpoint.json")
    checkpoint()
    timer=threading.Timer(budget.remaining(),lambda:os._exit(124)) if hard_deadline else None
    if timer: timer.daemon=True; timer.start()
    def write(identifier,body,version="1",title=None):
        text=f'---\nid: {identifier}\ntitle: {title or identifier.title()+" Fictional Guide"}\nversion: {version}\ntopic: {identifier}\n---\n{body}\n'
        (sources/(identifier+".md")).write_text(text,encoding="utf-8",newline="\n")
        save_new(output/"sources"/(identifier+"-"+version+".json"),{"filename":identifier+".md","source":text})
    previous=None
    def verify_stage(stage,result,active_ids,reused,new,counts_before,started):
        nonlocal previous
        budget.finish(); preserve(provenance["original_artifacts"])
        current=inspect_index(settings,sources,active_ids)
        new_by_id={p["id"]:p for p in current["chunks"]}
        old_by_id={p["id"]:p for p in previous["chunks"]} if previous else {}
        retained=[]
        for cid,old in old_by_id.items():
            if cid in new_by_id and old["text"]==new_by_id[cid]["text"]:
                if old["embedding_sha256"]!=new_by_id[cid]["embedding_sha256"]: raise StopRun("unchanged_vector_modified")
                retained.append(cid)
        if len(retained)!=reused: raise StopRun("reuse_count_mismatch")
        if stage!="initial" and (result["vectors_reused"],result["vectors_required"])!=(reused,new): raise StopRun("preview_reuse_mismatch")
        delta={k:budget.counts[k]-counts_before[k] for k in budget.counts}
        if delta!={"embedding_posts":1 if new else 0,"metadata_gets":2 if new else 0}: raise StopRun("operation_request_count_mismatch")
        if stage=="no-op-reindex" and (not result["no_op"] or result["applied"] or current["index_sha256"]!=previous["index_sha256"]): raise StopRun("noop_modified_index")
        record={"operation":stage,"status":"PASS","vectors_reused":reused,"vectors_generated":new,"retained_vector_chunk_ids":retained,
                "request_delta":delta,"seconds":round(time.monotonic()-started,6),"index_sha256":current["index_sha256"],
                "sqlite_fts_source_identity_checks":"PASS"}
        save_new(output/"snapshots"/(stage+".json"),current)
        previous=current; report["operations"].append(record); report["request_counts"]=dict(budget.counts)
        journal.write({"event":"operation_verified",**record}); checkpoint(); budget.remaining()
    try:
        with guarded_access(settings.db_path,budget,journal,output,transport_factory):
            write("selene","The selene recovery token remains valid for 32 minutes.")
            write("torin","The torin archive grace period is 19 days.")
            for stage in STAGES:
                budget.begin(stage); started=time.monotonic(); counts_before=dict(budget.counts)
                if stage=="initial":
                    result=ingest(sources,settings,adapter)
                    if (result["documents"],result["chunks"])!=(2,2): raise StopRun("initial_chunk_count_mismatch")
                    active,reused,new={"selene","torin"},0,2
                else:
                    operation="update"; options={"document_id":"wren","directory":sources,"filename":"wren.md","binding":adapter.binding}
                    if stage=="add":
                        write("wren","The wren service pass remains valid for 14 minutes.")
                        operation="add"; options.pop("document_id"); reused,new=2,1
                    elif stage=="content-update":
                        stale=preview(settings,"update",**options)
                        write("wren","The wren service pass remains valid for 22 minutes.","2")
                        reused,new=2,1
                    elif stage=="metadata-update":
                        write("wren","The wren service pass remains valid for 22 minutes.","2.1","Expanded Wren Fictional Support Guide")
                        reused,new=3,0
                    elif stage=="no-op-reindex":
                        operation="reindex"; options={"binding":adapter.binding}; reused,new=3,0
                    else:
                        operation="delete"; options={"document_id":"wren"}; reused,new=2,0
                    proposal=preview(settings,operation,**options)
                    save_new(output/"previews"/(stage+".json"),proposal)
                    journal.write({"event":"explicit_preview_confirmation","stage":stage,"confirmation":proposal["confirmation"],"authorization":"owner-approved fixed sequence"})
                    result=apply(settings,proposal,adapter,confirmation=proposal["confirmation"])
                    active={"selene","torin"} if stage=="delete" else {"selene","torin","wren"}
                verify_stage(stage,result,active,reused,new,counts_before,started)
                if stage=="content-update":
                    # Required negative guard verification: zero requests, zero writes,
                    # no reattempt of the successful content update and no adapter.
                    before=sha(settings.db_path); counters=dict(budget.counts)
                    try: apply(settings,stale,confirmation=stale["confirmation"])
                    except ValueError as exc:
                        if "Stale preview" not in str(exc): raise StopRun("unexpected_stale_preview_rejection") from exc
                    else: raise StopRun("stale_preview_accepted")
                    if counters!=budget.counts or sha(settings.db_path)!=before: raise StopRun("stale_check_had_side_effects")
                    report["stale_preview_negative_check"]="PASS; expected rejection, zero requests/writes"
            if budget.counts!={"embedding_posts":3,"metadata_gets":6}: raise StopRun("final_request_count_mismatch")
            preserve(provenance["original_artifacts"])
            report.update(status="PASS",final_index_sha256=sha(settings.db_path),deleted_document_absent_from_sql_fts_and_semantic_candidates=True,
                          historical_artifacts_preserved=len(provenance["original_artifacts"]),preservation="PASS")
    except BaseException as exc:
        report.update(status="STOPPED",failure={"type":type(exc).__name__,"reason":str(exc)})
        journal.write({"event":"run_stopped","failure":report["failure"],"counters":dict(budget.counts)})
    finally:
        report.update(request_counts=dict(budget.counts),seconds=round(budget.clock()-budget.started,6),ended_at_utc=utc())
        checkpoint(); save_new(output/"report.json",report)
        manifest={p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob("*")) if p.is_file()}
        save_new(output/"artifacts.sha256.json",manifest)
        if timer: timer.cancel()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-authorized-once",action="store_true")
    args=parser.parse_args()
    if not args.execute_authorized_once: parser.error("Explicit one-use authorization flag required; no requests made")
    budget=Budget()
    report=run(preflight(),budget)
    print(json.dumps({k:report.get(k) for k in ("status","request_counts","seconds","failure")},indent=2))
    if report["status"]!="PASS": raise SystemExit(1)


if __name__=="__main__": main()
