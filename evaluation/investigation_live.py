"""Fixed 4B preparation. Live flag requires separate explicit owner authorization.

Default/check mode has no network, claim or workspace side effects. No ID/path,
model, retry, resume or fallback overrides are offered.
"""
import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import url2pathname
import httpx
from evaluation.lifecycle_live import Journal, StopRun, encoded, save_new, sha, utc
from support_agent.config import Settings
from support_agent.documents import local_path, read_document
from support_agent.embeddings import ManagedOllama, PINNED_MODEL, PINNED_DIGEST
from support_agent.integrity import checked_index
from support_agent.ollama import unit
from support_agent.store import connect, ingest

ROOT=Path(__file__).resolve().parents[1]
BASELINE="00261c7f421ad98ab980985c1e26ffb0beac7af4"
RUN_ID="investigation-nomic-4b-once"
PREPARATION=Path("reports/investigation-live-preparation")
PLAN_SHA256="d2f68baa13c4732a7d5a88c6fce2ffa04a6cbb4ea496ba08cc527a6c7882dec2"
PRESERVATION_SHA256="f33c8f5df9d1bb763156f5e09e0cfc2091a3c119f0aeb3842c2d9ab7c433aab0"
PROOF=Path(".local/investigation-4b-offline-proof.json")
TEST_LOG=Path(".local/investigation-4b-offline-tests.log")
SOURCE_FILES=("evaluation/investigation_live.py","support_agent/investigation/managed.py",
              "support_agent/investigation/tools.py","support_agent/investigation/workflow.py",
              "support_agent/investigation/mcp_server.py","tests/test_investigation_managed.py",
              "tests/test_investigation_live.py",".github/workflows/investigation.yml",
              "requirements-investigation.lock",(PREPARATION/"plan.json").as_posix(),
              (PREPARATION/"preservation.json").as_posix())
ALLOWED_CHANGES=set(SOURCE_FILES)-{"requirements-investigation.lock"}|{
    "docs/investigation-live-preparation.md", *[(PREPARATION/n).as_posix() for n in
    ("report.md","offline.json","coverage.json")]}


def frozen(name,expected):
    path=ROOT/PREPARATION/name
    if sha(path)!=expected: raise StopRun("frozen_preparation_changed:"+name)
    return json.loads(path.read_text(encoding="utf-8"))


def plan():
    return frozen("plan.json",PLAN_SHA256)


def fixed_paths():
    value=plan()
    paths={"workspace":f".local/{RUN_ID}","database":f".local/{RUN_ID}/fictional.sqlite3",
           "claim":f".local/{RUN_ID}.claim.json","output":f"reports/{RUN_ID}"}
    if value["run_id"]!=RUN_ID or value["predecessor"]!=BASELINE or any(value[k]!=v for k,v in paths.items()):
        raise StopRun("fixed_configuration_mismatch")
    return {k:ROOT/v for k,v in paths.items()}


def unused_paths():
    paths=fixed_paths()
    for parent in (ROOT/".local",ROOT/"reports"):
        local_path(parent if parent.exists() or parent.is_symlink() else parent.parent)
    if any(p.exists() or p.is_symlink() for p in paths.values()):
        raise StopRun("claim_workspace_or_output_already_exists")
    return paths


def preservation(require_local=False):
    snapshot=frozen("preservation.json",PRESERVATION_SHA256)
    checked={}; skipped=[]
    for name,expected in snapshot["files"].items():
        path=ROOT/name
        if not path.exists() and not require_local and name in snapshot["optional_in_fresh_ci"]:
            skipped.append(name); continue
        if sha(path) not in {expected,snapshot["git_representations"].get(name,expected)}:
            raise StopRun("historical_artifact_changed:"+name)
        checked[name]=sha(path)
    for name in snapshot["required_absent"]:
        if (ROOT/name).exists() or (ROOT/name).is_symlink(): raise StopRun("historical_absence_changed")
    return {"checked":checked,"skipped_optional":skipped}


def git(*args):
    return subprocess.check_output(["git","-c","safe.directory="+ROOT.as_posix(),*args],cwd=ROOT,timeout=10)


def sources():
    return {name:sha(ROOT/name) for name in SOURCE_FILES}


def record_offline_proof(exit_code):
    text=(ROOT/TEST_LOG).read_text(encoding="utf-8-sig")
    if exit_code!=0 or not re.search(r"(?m)^\d+ passed(?:,| in )",text) or re.search(r"(?m)^.*\d+ failed",text):
        raise StopRun("successful_offline_test_log_required")
    value={"commit":git("rev-parse","HEAD").decode().strip(),"exit_code":0,"source_sha256":sources(),
           "log_sha256":sha(ROOT/TEST_LOG),"recorded_at_utc":utc()}
    temp=ROOT/PROOF.with_suffix(".tmp")
    with temp.open("wb") as stream:
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp,ROOT/PROOF)
    return value


def preflight():
    if git("status","--porcelain").strip(): raise StopRun("working_tree_not_clean")
    head=git("rev-parse","HEAD").decode().strip()
    git("merge-base","--is-ancestor",BASELINE,head)
    if set(git("diff","--name-only",BASELINE,head).decode().splitlines())-ALLOWED_CHANGES:
        raise StopRun("unexpected_source_or_evidence_change")
    paths=unused_paths()
    history=preservation(require_local=True)
    proof=json.loads((ROOT/PROOF).read_text(encoding="utf-8"))
    if (proof.get("commit")!=head or proof.get("exit_code")!=0 or proof.get("source_sha256")!=sources()
        or proof.get("log_sha256")!=sha(ROOT/TEST_LOG)):
        raise StopRun("offline_proof_missing_or_stale")
    # SDK import/version inspection is local only, never Ollama metadata.
    from importlib.metadata import version
    from support_agent.investigation.workflow import tracing_guard
    tracing_guard()
    if version("mcp")!="2.3.0" or version("langgraph")!="1.2.22": raise StopRun("sdk_identity_mismatch")
    return {"run_id":RUN_ID,"predecessor":BASELINE,"runner_commit":head,"plan_sha256":PLAN_SHA256,
            "preservation_sha256":PRESERVATION_SHA256,"paths":{k:str(v) for k,v in paths.items()},
            "offline_proof":proof,"original_artifacts":history["checked"]}


class Budget:
    def __init__(self,clock=time.monotonic):
        self.clock=clock; self.started=clock(); self.counts={"embedding_posts":0,"metadata_gets":0}
        self.stage=None; self.stages=[]; self.slot=0; self.failed=False; self.sealed=False

    def remaining(self):
        left=600-(self.clock()-self.started)
        if left<=0: raise StopRun("overall_timeout")
        return left

    def begin(self,stage):
        self.remaining()
        expected=("ingestion","supported","unsupported")
        if self.failed or len(self.stages)>=3 or stage!=expected[len(self.stages)]: raise StopRun("repeated_or_unexpected_stage")
        if self.stage and not self.sealed: raise StopRun("unfinished_stage")
        self.stage=stage; self.stages.append(stage); self.slot=0; self.sealed=False

    def finish(self):
        self.remaining()
        if self.failed or self.slot!=3: raise StopRun("uncertain_or_incomplete_budget")
        self.sealed=True

    def reserve(self,request):
        try:
            self.remaining()
            if self.failed or self.sealed or self.stage is None or self.slot>=3: raise StopRun("request_outside_stage_or_retry")
            if str(request.url.copy_with(path="/",query=None))!="http://127.0.0.1:11434/" or request.url.query:
                raise StopRun("unauthorized_origin")
            if any(k in request.headers for k in ("authorization","proxy-authorization")): raise StopRun("unexpected_credentials")
            expected=("GET","/api/tags") if self.slot in (0,2) else ("POST","/api/embed")
            if (request.method,request.url.path)!=expected: raise StopRun("unauthorized_endpoint_or_order")
            if request.method=="POST":
                if len(request.content)>4096: raise StopRun("embedding_payload_limit")
                data=json.loads(request.content)
                value=plan()
                texts=(["search_document: "+d["quote"] for d in value["documents"]] if self.stage=="ingestion" else
                       ["search_query: "+next(q["question"] for q in value["questions"] if q["id"]==self.stage)])
                expected={"model":PINNED_MODEL,"input":texts,"truncate":False,"keep_alive":"5m","options":{"num_ctx":2048}}
                if json.dumps(data,sort_keys=True,allow_nan=False)!=json.dumps(expected,sort_keys=True):
                    raise StopRun("unapproved_embedding_payload")
                kind,cap="embedding_posts",3
            else:
                if request.content: raise StopRun("unexpected_metadata_payload")
                kind,cap="metadata_gets",6
            if self.counts[kind]>=cap: raise StopRun("request_budget")
            self.counts[kind]+=1; self.slot+=1
            return kind,sum(self.counts.values())
        except BaseException:
            self.failed=True
            raise


class RecordedTransport(httpx.AsyncBaseTransport):
    def __init__(self,budget,journal,output,inner):
        self.budget,self.journal,self.output,self.inner=budget,journal,output,inner

    async def handle_async_request(self,request):
        kind,sequence=self.budget.reserve(request)
        record={"sequence":sequence,"stage":self.budget.stage,"kind":kind,"method":request.method,
                "endpoint":request.url.path,"counters":dict(self.budget.counts),"request_sha256":sha_bytes(request.content)}
        started=time.monotonic()
        try:
            save_new(self.output/"requests"/f"{sequence:03d}.json",{"method":request.method,"url":str(request.url),
                     "payload":json.loads(request.content) if request.content else None})
            self.journal.write({"event":"request_attempt",**record}) # fsync BEFORE network
            async with asyncio.timeout(min(90,self.budget.remaining())):
                response=await self.inner.handle_async_request(request)
                try:
                    raw=bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw)>2_000_000: raise StopRun("response_size_limit")
                finally: await response.aclose()
            path=self.output/"responses"/f"{sequence:03d}.body"
            with path.open("xb") as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            record.update(status_code=response.status_code,response_sha256=sha(path))
            if response.status_code!=200: raise StopRun("failed_model_response")
            body=json.loads(raw)
            if not isinstance(body,dict): raise StopRun("malformed_model_response")
            if kind=="metadata_gets":
                models=body.get("models")
                if not isinstance(models,list) or any(not isinstance(m,dict) for m in models): raise StopRun("invalid_metadata")
                matches=[m for m in models if m.get("name")==PINNED_MODEL]
                if len(matches)!=1 or matches[0].get("digest")!=PINNED_DIGEST or matches[0].get("remote_host") or matches[0].get("remote_model"):
                    raise StopRun("model_identity_mismatch")
                record["verified_identity"]=matches[0]
            else:
                vectors=body.get("embeddings")
                count=2 if self.budget.stage=="ingestion" else 1
                if (body.get("model")!=PINNED_MODEL or body.get("remote_host") or body.get("remote_model")
                    or not isinstance(vectors,list) or len(vectors)!=count or any(len(unit(v))!=768 for v in vectors)):
                    raise StopRun("invalid_embedding_response")
                record.update(model=body["model"],dimension=768,embedding_count=count)
            self.budget.remaining()
            record["outcome"]="verified_response"
            return httpx.Response(200,content=bytes(raw),request=request)
        except BaseException as exc:
            self.budget.failed=True
            record.update(outcome="failed",error_type=type(exc).__name__)
            raise
        finally:
            record["seconds"]=round(time.monotonic()-started,6)
            self.journal.write({"event":"request_finished",**record})

    async def aclose(self):
        await self.inner.aclose()


def sha_bytes(value):
    import hashlib
    return hashlib.sha256(value).hexdigest()


@contextmanager
def guarded_access(database,budget,journal,output,transport_factory):
    original_connect=sqlite3.connect; original_client=httpx.AsyncClient
    def db_connect(value,*args,**kwargs):
        if str(value).startswith("file:"):
            uri=urlsplit(str(value))
            if kwargs.get("uri") is not True or uri.netloc or uri.query!="mode=ro" or uri.fragment:
                raise StopRun("unauthorized_database_uri")
            target=Path(url2pathname(uri.path)); readonly=True
        else:
            target=Path(value); readonly=False
        if target.resolve()!=database.resolve() or budget.stage!="ingestion" and not readonly:
            raise StopRun("unauthorized_database_access")
        budget.remaining()
        journal.write({"event":"database_open","stage":budget.stage,"readonly":readonly,"database":str(target.resolve())})
        return original_connect(value,*args,**kwargs)
    def client(**kwargs):
        if kwargs.get("trust_env") is not False or kwargs.get("follow_redirects") is not False or "transport" in kwargs:
            raise StopRun("unsafe_http_configuration")
        inner=transport_factory() if transport_factory else httpx.AsyncHTTPTransport(retries=0)
        return original_client(transport=RecordedTransport(budget,journal,output,inner),**kwargs)
    with patch("sqlite3.connect",db_connect),patch("httpx.AsyncClient",client):
        yield


def inspect_index(settings,documents):
    before=sha(settings.db_path)
    with connect(settings.db_path) as db:
        rows,dimension=checked_index(db,settings)
        meta=dict(db.execute("SELECT key,value FROM metadata"))
        if dimension!=768 or meta.get("embed_digest")!=PINNED_DIGEST: raise StopRun("index_identity_failure")
        if len(rows)!=2 or {p["id"] for p in rows}!={d["chunk_id"] for d in documents}: raise StopRun("index_membership_failure")
        for d in documents:
            p=next(p for p in rows if p["id"]==d["chunk_id"])
            if len(p["quotes"])!=1 or any(p["quotes"][0][k]!=d[k] for k in ("quote","source_start","source_end","normalized_start","normalized_end")):
                raise StopRun("source_offset_failure")
        if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok" or db.execute("PRAGMA foreign_key_check").fetchone():
            raise StopRun("sqlite_integrity_failure")
        hashes={r["id"]:sha_bytes(r["embedding"].encode()) for r in db.execute("SELECT id,embedding FROM chunks ORDER BY id")}
    if sha(settings.db_path)!=before: raise StopRun("readonly_index_changed")
    return {"index_sha256":before,"dimension":dimension,"metadata":meta,"vector_sha256":hashes,
            "source_offsets_verified":True,"fts5_correspondence_verified":True,"sqlite_verified":True}


def execute(provenance,budget,*,transport_factory=None,hard_deadline=True):
    if os.environ.get("PYTEST_CURRENT_TEST") and transport_factory is None: raise StopRun("offline_tests_require_fake_transport")
    if not hard_deadline and transport_factory is None: raise StopRun("live_deadline_required")
    paths=unused_paths(); value=plan()
    history=preservation(require_local=transport_factory is None)
    if (provenance.get("run_id")!=RUN_ID or provenance.get("predecessor")!=BASELINE
        or provenance.get("paths")!={k:str(v) for k,v in paths.items()} or provenance.get("plan_sha256")!=PLAN_SHA256
        or provenance.get("original_artifacts")!=history["checked"]):
        raise StopRun("execution_provenance_mismatch")
    save_new(paths["claim"],{"run_id":RUN_ID,"consumed":True,"claimed_at_utc":utc(),"runner_commit":provenance["runner_commit"],
             "plan_sha256":PLAN_SHA256,"limits":value["limits"],"database":str(paths["database"]),"rule":"Never delete, reuse or resume this claim"})
    paths["workspace"].mkdir(); paths["output"].mkdir()
    output=paths["output"]; sources_dir=paths["workspace"]/"fictional-sources"; sources_dir.mkdir()
    (output/"responses").mkdir()
    with (output/".gitattributes").open("xb") as stream: stream.write(b"*.body -text\n")
    journal=Journal(output/"requests.jsonl")
    save_new(output/"run-claim.json",json.loads(paths["claim"].read_text()))
    save_new(output/"preflight.json",provenance); save_new(output/"plan.json",value)
    settings=replace(Settings(),db_path=paths["database"],managed_embeddings=True)
    report={"run_id":RUN_ID,"kind":"single bounded fictional local embedding-backed investigation; no generation",
            "status":"INCOMPLETE_NOT_PASS","source_baseline":BASELINE,"runner_commit":provenance["runner_commit"],
            "plan_sha256":PLAN_SHA256,"model":PINNED_MODEL,"required_digest":PINNED_DIGEST,"operations":[],
            "generation_posts":0,"downloads":0,"retries":0,"substitutions":0,"started_at_utc":utc()}
    def checkpoint():
        report.update(request_counts=dict(budget.counts),seconds=round(budget.clock()-budget.started,6))
        temp=output/"checkpoint.tmp"
        with temp.open("wb") as stream:
            stream.write(encoded(report)); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp,output/"checkpoint.json")
    checkpoint()
    timer=threading.Timer(budget.remaining(),lambda:os._exit(124)) if hard_deadline else None
    if timer: timer.daemon=True; timer.start()
    try:
        from support_agent.investigation.contracts import Limits
        from support_agent.investigation.managed import ManagedQuery
        from support_agent.investigation.tools import ReadOnlyTools
        from support_agent.investigation.workflow import investigate
        with guarded_access(settings.db_path,budget,journal,output,transport_factory):
            budget.begin("ingestion")
            for d in value["documents"]:
                path=sources_dir/d["filename"]; path.write_text(d["source"],encoding="utf-8",newline="\n")
                if sha(path)!=d["sha256"] or len(read_document(sources_dir,d["filename"],settings)[1])!=1:
                    raise StopRun("fictional_source_integrity_failure")
            result=ingest(sources_dir,settings,ManagedOllama(settings))
            budget.finish()
            if result.get("documents")!=2 or result.get("chunks")!=2: raise StopRun("ingestion_count_mismatch")
            initial=inspect_index(settings,value["documents"]); save_new(output/"index-initial.json",initial)
            if preservation(require_local=transport_factory is None)["checked"]!=history["checked"]: raise StopRun("preservation_failure")
            report["operations"].append({"stage":"ingestion","status":"PASS","request_delta":{"embedding_posts":1,"metadata_gets":2},"index":initial})
            checkpoint()
            adapter=ManagedQuery(settings,max_queries=2)
            for question in value["questions"]:
                budget.begin(question["id"]); started=budget.clock(); before=dict(budget.counts)
                runtime=ReadOnlyTools(settings,query_adapter=adapter,limits=Limits(tool_seconds=5.0,total_seconds=30.0))
                response=asyncio.run(investigate({"question":question["question"]},runtime))
                save_new(output/(question["id"]+".json"),response)
                save_new(output/(question["id"]+"-tool-receipts.json"),runtime._issued)
                budget.finish()
                if response["abstained"]!=question["abstain"] or response["diagnostics"]["model_calls"]!=1:
                    raise StopRun("question_expectation_failure:"+question["id"])
                if not question["abstain"]:
                    if len(response["citations"])!=1: raise StopRun("citation_count_failure")
                    c=response["citations"][0]; d=value["documents"][0]
                    if any(c[k]!=d[k] for k in ("chunk_id","quote","source_start","source_end","normalized_start","normalized_end")):
                        raise StopRun("citation_expectation_failure")
                elif response["citations"] or response["diagnostics"]["reason"] not in question["allowed_verification_reasons"]:
                    raise StopRun("unsafe_or_unexpected_abstention")
                current=inspect_index(settings,value["documents"])
                if current!=initial: raise StopRun("investigation_modified_index")
                delta={k:budget.counts[k]-before[k] for k in before}
                if delta!={"embedding_posts":1,"metadata_gets":2}: raise StopRun("question_budget_failure")
                report["operations"].append({"stage":question["id"],"status":"PASS","abstained":response["abstained"],
                    "reason":response["diagnostics"]["reason"],"seconds":round(budget.clock()-started,6),"request_delta":delta,
                    "model_calls":1,"steps":response["diagnostics"]["steps"],"tool_calls":response["diagnostics"]["tool_calls"],
                    "index_unchanged":True,"citation_offsets_verified":True})
                if preservation(require_local=transport_factory is None)["checked"]!=history["checked"]: raise StopRun("preservation_failure")
                checkpoint()
            if budget.counts!={"embedding_posts":3,"metadata_gets":6}: raise StopRun("final_budget_failure")
            if sha(paths["claim"])!=sha(output/"run-claim.json"): raise StopRun("consumed_claim_changed")
            budget.remaining()
            report.update(status="PASS",final_index_sha256=initial["index_sha256"],preserved_files=len(history["checked"]))
    except BaseException as exc:
        budget.failed=True
        report.update(status="STOPPED",failure={"type":type(exc).__name__,"reason":str(exc)})
        journal.write({"event":"run_stopped","counters":dict(budget.counts),"failure":report["failure"]})
    finally:
        try:
            if preservation(require_local=transport_factory is None)["checked"]!=history["checked"]:
                raise StopRun("preservation_failure")
            if sha(paths["claim"])!=sha(output/"run-claim.json"): raise StopRun("consumed_claim_changed")
            report["preservation"]="PASS"
        except BaseException as exc:
            report.update(status="STOPPED",preservation="FAIL",failure={"type":type(exc).__name__,"reason":str(exc)})
        report["ended_at_utc"]=utc()
        checkpoint(); save_new(output/"report.json",report)
        save_new(output/"artifacts.sha256.json",{p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob("*")) if p.is_file()})
        if timer: timer.cancel()
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-preparation",action="store_true")
    modes.add_argument("--execute-authorized-once",action="store_true",help="Requires separate explicit owner approval of the exact source commit and fixed plan")
    args=parser.parse_args(argv)
    if args.check_preparation:
        value=preflight()
        print(json.dumps({"status":"READY_FOR_SEPARATE_AUTHORIZATION","commit":value["runner_commit"],"run_id":RUN_ID,
                          "paths":value["paths"],"protected_files":len(value["original_artifacts"]),"model_requests":0,"claim_consumed":False},indent=2))
        return
    budget=Budget()
    timer=threading.Timer(budget.remaining(),lambda:os._exit(124))
    timer.daemon=True; timer.start()
    try:
        report=execute(preflight(),budget)
        budget.remaining()
    finally: timer.cancel()
    print(json.dumps({k:report[k] for k in ("status","request_counts","seconds")},indent=2))
    if report["status"]!="PASS": raise SystemExit(1)


if __name__=="__main__": main()
