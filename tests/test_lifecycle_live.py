"""The authorized runner is tested only with fake HTTP and fresh temporary SQLite."""
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import httpx
import pytest
from evaluation import lifecycle_live as runner


def request(method="GET",endpoint="/api/tags",count=2,**changes):
    payload={"model":runner.PINNED_MODEL,"input":["search_document: Fictional note."]*count,
             "truncate":False,"keep_alive":"5m","options":{"num_ctx":2048}}
    payload.update(changes)
    return httpx.Request(method,"http://127.0.0.1:11434"+endpoint,json=payload) if method=="POST" else httpx.Request(method,"http://127.0.0.1:11434"+endpoint)


def three(budget,count):
    budget.reserve(request())
    budget.reserve(request("POST","/api/embed",count))
    budget.reserve(request())


def test_budget_exact_ceilings_and_zero_call_stages():
    b=runner.Budget(clock=lambda:0)
    for stage,count in runner.COUNTS.items():
        b.begin(stage); three(b,count); b.finish()
        with pytest.raises(runner.StopRun): b.reserve(request())
    for stage in runner.STAGES[3:]:
        b.begin(stage); b.finish()
        with pytest.raises(runner.StopRun): b.reserve(request())
    assert b.counts=={"embedding_posts":3,"metadata_gets":6}
    with pytest.raises(runner.StopRun): b.begin("initial")


@pytest.mark.parametrize("method,endpoint",[("POST","/api/chat"),("POST","/api/pull"),("GET","/api/version"),("GET","/api/embed"),("POST","/api/tags")])
def test_forbidden_endpoints_never_consume_or_send_a_request(method,endpoint):
    b=runner.Budget(); b.begin("initial")
    with pytest.raises(runner.StopRun): b.reserve(request(method,endpoint))
    assert sum(b.counts.values())==0


@pytest.mark.parametrize("changes",[{"model":"substitution"},{"input":["search_query: text"]*2},{"input":["search_document: text"]*17},{"truncate":True},{"options":{"num_ctx":9999}}])
def test_queries_wrong_identity_and_payloads_are_forbidden(changes):
    b=runner.Budget(); b.begin("initial"); b.reserve(request())
    with pytest.raises(runner.StopRun): b.reserve(request("POST","/api/embed",**changes))
    assert b.counts=={"embedding_posts":0,"metadata_gets":1}


def test_deadline_origin_order_and_operation_attempt_guards():
    now=[0]
    b=runner.Budget(clock=lambda:now[0]); b.begin("initial")
    for url in ("https://127.0.0.1:11434/api/tags","http://example.com/api/tags","http://127.0.0.1:11434/api/tags?extra=1"):
        with pytest.raises(runner.StopRun): b.reserve(httpx.Request("GET",url))
    with pytest.raises(runner.StopRun): b.begin("add")
    now[0]=600
    with pytest.raises(runner.StopRun,match="timeout"): b.reserve(request())
    assert not sum(b.counts.values())


@pytest.fixture
def fake_workspace(tmp_path,monkeypatch):
    monkeypatch.setattr(runner,"ROOT",tmp_path)
    (tmp_path/"reports").mkdir()
    historical=tmp_path/"historical.txt"; historical.write_bytes(b"synthetic preserved history")
    provenance={"runner_commit":"synthetic-offline-test","approved_source_commit":runner.BASELINE,
                "original_artifacts":{"historical.txt":runner.sha(historical)}}
    return tmp_path,provenance


def fake_transport(root,mode="valid"):
    events=[]
    def respond(req):
        events.append((req.method,req.url.path))
        ledger=[json.loads(line) for line in (root/"reports"/runner.RUN_ID/"requests.jsonl").read_text().splitlines()]
        attempts=[e for e in ledger if e["event"]=="request_attempt"]
        assert len(attempts)==len(events) and attempts[-1]["sequence"]==len(events) # pre-send durability
        if mode=="connection": raise httpx.ConnectError("synthetic failure",request=req)
        if mode=="status": return httpx.Response(503,json={"error":"synthetic"})
        if req.method=="GET":
            digest="0"*64 if mode=="mismatch" or mode=="changed-after" and len(events)==3 else runner.PINNED_DIGEST
            return httpx.Response(200,json={"models":[{"name":runner.PINNED_MODEL,"digest":digest,"size":123}]})
        data=json.loads(req.content)
        if mode=="bad-vector": return httpx.Response(200,json={"model":runner.PINNED_MODEL,"embeddings":[[0]*768]*len(data["input"])})
        if mode=="malformed": return httpx.Response(200,content=b"not json")
        vectors=[]
        for text in data["input"]:
            raw=hashlib.sha256(text.encode()).digest()
            vectors.append([float(v+1) for v in raw]+[0.0]*736)
        return httpx.Response(200,json={"model":runner.PINNED_MODEL,"embeddings":vectors})
    return lambda:httpx.MockTransport(respond),events


def test_full_fake_lifecycle_exercises_unchanged_production_adapter_and_storage(fake_workspace):
    root,proof=fake_workspace
    transport,events=fake_transport(root)
    report=runner.run(proof,runner.Budget(),transport_factory=transport,hard_deadline=False)
    assert report["status"]=="PASS",report
    assert report["request_counts"]=={"embedding_posts":3,"metadata_gets":6}
    assert [r["vectors_reused"] for r in report["operations"]]==[0,2,2,3,3,2]
    assert [r["vectors_generated"] for r in report["operations"]]==[2,1,1,0,0,0]
    assert report["stale_preview_negative_check"].startswith("PASS")
    assert len(events)==9 and report["generation_posts"]==report["query_embeddings"]==0
    assert all(r["request_delta"]=={"embedding_posts":0,"metadata_gets":0} for r in report["operations"][3:])
    claim=root/".local"/(runner.RUN_ID+".claim.json")
    before=claim.read_bytes()
    with pytest.raises(FileExistsError): runner.run(proof,runner.Budget(),transport_factory=transport,hard_deadline=False)
    assert claim.read_bytes()==before and len(events)==9
    manifest=json.loads((root/"reports"/runner.RUN_ID/"artifacts.sha256.json").read_text())
    assert all(runner.sha(root/"reports"/runner.RUN_ID/name)==digest for name,digest in manifest.items())


@pytest.mark.parametrize("mode,posts,gets",[("mismatch",0,1),("connection",0,1),("status",0,1),("bad-vector",1,1),("malformed",1,1),("changed-after",1,2)])
def test_failed_attempts_are_counted_claim_consumed_and_no_later_calls(fake_workspace,mode,posts,gets):
    root,proof=fake_workspace
    transport,events=fake_transport(root,mode)
    report=runner.run(proof,runner.Budget(),transport_factory=transport,hard_deadline=False)
    assert report["status"]=="STOPPED"
    assert report["request_counts"]=={"embedding_posts":posts,"metadata_gets":gets}
    assert len(events)==posts+gets
    assert (root/".local"/(runner.RUN_ID+".claim.json")).exists()
    ledger=[json.loads(line) for line in (root/"reports"/runner.RUN_ID/"requests.jsonl").read_text().splitlines()]
    assert len([e for e in ledger if e["event"]=="request_attempt"])==posts+gets
    assert not report["operations"]


def test_unexpected_database_access_is_rejected_before_sqlite_connect(tmp_path):
    allowed=tmp_path/"only.sqlite3"
    other=tmp_path/"historical.sqlite3"; other.write_bytes(b"untouched")
    output=tmp_path/"output"; output.mkdir()
    with runner.guarded_access(allowed,runner.Budget(),runner.Journal(output/"journal.jsonl"),output,lambda:httpx.MockTransport(lambda r:httpx.Response(500))):
        with pytest.raises(runner.StopRun,match="database_access"): sqlite3.connect(other)
    assert other.read_bytes()==b"untouched" and not allowed.exists()


def test_journal_failure_prevents_the_transport_call(tmp_path,monkeypatch):
    b=runner.Budget(); b.begin("initial")
    j=runner.Journal(tmp_path/"journal.jsonl")
    events=[]
    monkeypatch.setattr(j,"write",lambda _:(_ for _ in ()).throw(OSError("disk failure")))
    transport=runner.RecordedTransport(b,j,tmp_path,httpx.MockTransport(lambda r:events.append(r)))
    with pytest.raises(OSError): asyncio.run(transport.handle_async_request(request()))
    assert not events and b.counts["metadata_gets"]==1


def test_deadline_stops_slow_transport_and_preserves_attempt(tmp_path):
    b=runner.Budget(clock=lambda:0); b.begin("initial")
    j=runner.Journal(tmp_path/"journal.jsonl"); (tmp_path/"responses").mkdir()
    async def slow(req):
        await asyncio.sleep(.05)
        return httpx.Response(200,json={"models":[]})
    b.started-=599.99
    transport=runner.RecordedTransport(b,j,tmp_path,httpx.MockTransport(slow))
    with pytest.raises(TimeoutError): asyncio.run(transport.handle_async_request(request()))
    assert b.counts=={"embedding_posts":0,"metadata_gets":1}
    assert "request_attempt" in j.path.read_text() and "failed" in j.path.read_text()


def test_cli_default_never_runs_preflight_or_requests(monkeypatch):
    monkeypatch.setattr(sys,"argv",["lifecycle_live"])
    monkeypatch.setattr(runner,"preflight",lambda:(_ for _ in ()).throw(AssertionError("no execution")))
    with pytest.raises(SystemExit): runner.main()


def test_preflight_rejects_changed_approved_application_source(monkeypatch):
    def git(*args):
        if args[:2]==("status","--porcelain"): return b""
        if args[0]=="rev-parse": return b"synthetic-head"
        if args[0]=="merge-base": return b""
        if args[0]=="diff": return b"support_agent/store.py\n"
        raise AssertionError(args)
    monkeypatch.setattr(runner,"git",git)
    with pytest.raises(runner.StopRun,match="approved_source"): runner.preflight()


def test_recorded_real_lifecycle_evidence_if_published():
    output=Path("reports")/runner.RUN_ID
    if not output.exists(): pytest.skip("The one live acceptance has not been published; this is not mock acceptance")
    report=json.loads((output/"report.json").read_text())
    assert report["approved_source_commit"]==runner.BASELINE
    manifest=json.loads((output/"artifacts.sha256.json").read_text())
    assert all(runner.sha(output/name)==digest for name,digest in manifest.items())
    journal=[json.loads(line) for line in (output/"requests.jsonl").read_text().splitlines()]
    attempts=[e for e in journal if e["event"]=="request_attempt"]
    assert len(attempts)==sum(report["request_counts"].values())
    assert report["request_counts"]["embedding_posts"]<=3 and report["request_counts"]["metadata_gets"]<=6
    assert report["query_embeddings"]==report["generation_posts"]==0
    if report["status"]=="PASS":
        assert report["request_counts"]=={"embedding_posts":3,"metadata_gets":6}
        assert len(report["operations"])==6
        assert all(r["status"]=="PASS" for r in report["operations"])
        assert report["required_digest"]==runner.PINNED_DIGEST
