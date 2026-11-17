"""Single-use acceptance rehearsal: fake transport, temporary root, invented data."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import socket
import sqlite3
import httpx
import pytest
from evaluation import investigation_live as live

REAL_ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    loop=asyncio.new_event_loop()
    monkeypatch.setattr(asyncio,"run",lambda coro:loop.run_until_complete(coro))
    def blocked(*args,**kwargs): raise AssertionError("Real sockets forbidden")
    monkeypatch.setattr(socket.socket,"connect",blocked)
    monkeypatch.setattr(socket,"create_connection",blocked)
    yield
    loop.run_until_complete(loop.shutdown_asyncgens()); loop.close()


@pytest.fixture
def sandbox(tmp_path,monkeypatch):
    (tmp_path/".local").mkdir(); (tmp_path/"reports").mkdir()
    for name in live.SOURCE_FILES:
        target=tmp_path/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(REAL_ROOT/name,target)
    history={".local/phase-d-v2-once.claim.json":b'{"consumed":true}',
             ".local/lifecycle-real-once.claim.json":b'{"consumed":true,"status":"STOPPED"}',
             ".local/lifecycle-real-20261117-once.claim.json":b'{"consumed":true,"status":"PASS"}',
             ".local/historical.sqlite3":b"invented protected bytes; never opened as SQL",
             "labels-v2.json":b"invented frozen labels"}
    for name,body in history.items(): (tmp_path/name).write_bytes(body)
    snapshot={"files":{n:live.sha(tmp_path/n) for n in history},"optional_in_fresh_ci":[],
              "git_representations":{},"required_absent":[".local/old-failed.sqlite3"]}
    manifest=tmp_path/live.PREPARATION/"preservation.json"
    manifest.write_bytes(live.encoded(snapshot))
    monkeypatch.setattr(live,"ROOT",tmp_path)
    monkeypatch.setattr(live,"PRESERVATION_SHA256",live.sha(manifest))
    def git(*args):
        if args==("status","--porcelain"): return b""
        if args==("rev-parse","HEAD"): return b"f"*40
        if args[0]=="merge-base": return b""
        if args[0]=="diff": return b"support_agent/investigation/managed.py\n"
        raise AssertionError(args)
    monkeypatch.setattr(live,"git",git)
    (tmp_path/live.TEST_LOG).write_text("100 passed, 1 skipped in 1.0s\n",encoding="utf-8")
    live.record_offline_proof(0)
    return tmp_path,history


def fake_transport(root,mode="valid"):
    calls=[]
    async def respond(request):
        calls.append(request)
        ledger=[json.loads(s) for s in (root/"reports"/live.RUN_ID/"requests.jsonl").read_text().splitlines()]
        attempts=[e for e in ledger if e["event"]=="request_attempt"]
        assert len(attempts)==len(calls)  # journal is durable before this mock send
        if mode=="connection": raise httpx.ConnectError("invented failure",request=request)
        if mode=="read-timeout" and len(calls)==5: raise httpx.ReadTimeout("invented timeout",request=request)
        if request.method=="GET":
            digest=live.PINNED_DIGEST
            if mode=="identity" or mode=="after-identity" and len(calls)==3: digest="0"*64
            return httpx.Response(200,json={"models":[{"name":live.PINNED_MODEL,"digest":digest}]})
        if mode=="post-http": return httpx.Response(503,json={"error":"invented"})
        if mode=="json": return httpx.Response(200,content=b"not-json")
        if mode=="oversize": return httpx.Response(200,content=b"x"*2_000_001)
        inputs=json.loads(request.content)["input"]
        vectors=[[1.0]+[0.0]*767 for _ in inputs]
        if mode=="dimension": vectors[0].pop()
        if mode=="zero": vectors[0]=[0.0]*768
        return httpx.Response(200,json={"model":live.PINNED_MODEL,"embeddings":vectors})
    return lambda:httpx.MockTransport(respond),calls


def test_frozen_labels_and_all_prior_claims_remain_unchanged_and_new_paths_absent():
    assert live.plan()["expectations_frozen_before_testing"] is True
    assert live.plan()["limits"]=={"embedding_posts":3,"metadata_gets":6,"generation_posts":0,"seconds":600,
        "query_embeddings":2,"retries":0,"downloads":0,"substitutions":0,"cloud_calls":0,
        "graph_steps_per_question":20,"tool_attempts_per_question":4,"tool_seconds":5.0,"graph_seconds":30.0}
    result=live.preservation()
    assert len(result["checked"])+len(result["skipped_optional"])==309
    assert not any(p.exists() for p in live.unused_paths().values())
    assert live.plan()["questions"][0]["abstain"] is False and live.plan()["questions"][1]["abstain"] is True


def test_preparation_check_is_read_only_and_requires_current_offline_proof(sandbox,capsys,monkeypatch):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    root,_=sandbox
    paths=live.unused_paths()
    live.main(["--check-preparation"])
    assert json.loads(capsys.readouterr().out)["claim_consumed"] is False
    assert not any(p.exists() for p in paths.values())
    proof=root/live.PROOF; value=json.loads(proof.read_text()); value["commit"]="stale"
    proof.write_bytes(live.encoded(value))
    with pytest.raises(live.StopRun,match="offline_proof"): live.preflight()
    with pytest.raises(live.StopRun,match="successful_offline"): live.record_offline_proof(1)
    (root/live.TEST_LOG).write_text("1 failed, 99 passed\n")
    with pytest.raises(live.StopRun): live.record_offline_proof(0)


def test_complete_fake_run_uses_nine_requests_and_retains_consumed_claim(sandbox):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    root,history=sandbox; transport,calls=fake_transport(root)
    report=live.execute(live.preflight(),live.Budget(),transport_factory=transport,hard_deadline=False)
    assert report["status"]=="PASS",report
    assert report["request_counts"]=={"embedding_posts":3,"metadata_gets":6} and len(calls)==9
    assert [e["stage"] for e in report["operations"]]==["ingestion","supported","unsupported"]
    assert report["operations"][1]["abstained"] is False and report["operations"][2]["abstained"] is True
    assert all((root/n).read_bytes()==body for n,body in history.items())
    output=root/"reports"/live.RUN_ID
    assert live.sha(output/"run-claim.json")==live.sha(root/".local"/(live.RUN_ID+".claim.json"))
    assert len(json.loads((output/"supported-tool-receipts.json").read_text()))==3
    receipts=json.loads((output/"supported-tool-receipts.json").read_text())
    assert receipts[0][1]["data"]["passages"][0]["similarity"]==1.0
    manifest=json.loads((output/"artifacts.sha256.json").read_text())
    assert all(live.sha(output/n)==expected for n,expected in manifest.items())
    with pytest.raises(live.StopRun,match="already_exists"):
        live.execute({},live.Budget(),transport_factory=transport,hard_deadline=False)
    assert len(calls)==9
    # A clean clone may lack ignored local files; published evidence alone blocks reuse.
    shutil.rmtree(root/".local"/live.RUN_ID)
    (root/".local"/(live.RUN_ID+".claim.json")).unlink()
    with pytest.raises(live.StopRun,match="already_exists"): live.unused_paths()


@pytest.mark.parametrize("mode,count",[("connection",1),("identity",1),("after-identity",3),("post-http",2),
                                      ("json",2),("oversize",2),("dimension",2),("zero",2),("read-timeout",5)])
def test_failure_journal_and_claim_are_retained_without_retry(sandbox,mode,count):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    root,history=sandbox; transport,calls=fake_transport(root,mode)
    budget=live.Budget()
    report=live.execute(live.preflight(),budget,transport_factory=transport,hard_deadline=False)
    assert report["status"]=="STOPPED" and report["preservation"]=="PASS"
    assert len(calls)==count and sum(report["request_counts"].values())==count
    output=root/"reports"/live.RUN_ID
    ledger=[json.loads(s) for s in (output/"requests.jsonl").read_text().splitlines()]
    assert len([e for e in ledger if e["event"]=="request_attempt"])==count
    assert (root/".local"/(live.RUN_ID+".claim.json")).is_file() and (output/"report.json").is_file()
    assert budget.failed and all((root/n).read_bytes()==body for n,body in history.items())
    with pytest.raises(live.StopRun): budget.reserve(httpx.Request("GET","http://127.0.0.1:11434/api/tags"))
    with pytest.raises(live.StopRun): live.unused_paths()
    assert len(calls)==count


@pytest.mark.parametrize("method,url,payload",[("POST","http://127.0.0.1:11434/api/chat",{}),
    ("GET","http://127.0.0.1:11434/api/pull",None),("GET","https://external.invalid/api/tags",None),
    ("GET","http://127.0.0.1:11434/api/tags?q=extra",None),
    ("POST","http://127.0.0.1:11434/api/embed",{"model":"other"})])
def test_request_budget_rejects_external_generation_download_order_and_payload(method,url,payload):
    budget=live.Budget(); budget.begin("ingestion")
    with pytest.raises(live.StopRun): budget.reserve(httpx.Request(method,url,**({"json":payload} if payload is not None else {})))
    assert budget.failed and sum(budget.counts.values())==0


def test_budget_exact_prefixes_counts_stage_order_and_timeout():
    budget=live.Budget()
    with pytest.raises(live.StopRun): budget.begin("supported")
    budget.begin("ingestion")
    with pytest.raises(live.StopRun,match="unfinished"): budget.begin("supported")
    with pytest.raises(live.StopRun,match="incomplete"): budget.finish()
    for stage in ("ingestion","supported","unsupported"):
        if stage!="ingestion": budget.begin(stage)
        budget.reserve(httpx.Request("GET","http://127.0.0.1:11434/api/tags"))
        texts=(["search_document: "+d["quote"] for d in live.plan()["documents"]] if stage=="ingestion" else
               ["search_query: "+next(q["question"] for q in live.plan()["questions"] if q["id"]==stage)])
        budget.reserve(httpx.Request("POST","http://127.0.0.1:11434/api/embed",json={"model":live.PINNED_MODEL,
            "input":texts,"truncate":False,"keep_alive":"5m","options":{"num_ctx":2048}}))
        budget.reserve(httpx.Request("GET","http://127.0.0.1:11434/api/tags")); budget.finish()
    assert budget.counts=={"embedding_posts":3,"metadata_gets":6}
    with pytest.raises(live.StopRun): budget.reserve(httpx.Request("GET","http://127.0.0.1:11434/api/tags"))
    with pytest.raises(live.StopRun): budget.begin("ingestion")
    values=iter([0,600]); budget=live.Budget(clock=lambda:next(values))
    with pytest.raises(live.StopRun,match="overall_timeout"): budget.remaining()


def test_preexisting_request_caps_and_metadata_credentials_fail_before_send():
    for request in (httpx.Request("GET","http://127.0.0.1:11434/api/tags",headers={"Authorization":"invented"}),
                    httpx.Request("GET","http://127.0.0.1:11434/api/tags",content=b"unexpected")):
        budget=live.Budget(); budget.begin("ingestion")
        with pytest.raises(live.StopRun): budget.reserve(request)
        assert sum(budget.counts.values())==0
    budget=live.Budget(); budget.begin("ingestion"); budget.counts["metadata_gets"]=6
    with pytest.raises(live.StopRun,match="request_budget"):
        budget.reserve(httpx.Request("GET","http://127.0.0.1:11434/api/tags"))


@pytest.mark.parametrize("key,value",[("truncate",0),("input",["search_query: wrong question"]),("options",{"num_ctx":2049}),
                                      ("model","cloud:latest"),("keep_alive","forever")])
def test_budget_rejects_modified_ingestion_payloads_including_type_coercion(key,value):
    budget=live.Budget(); budget.begin("ingestion")
    budget.reserve(httpx.Request("GET","http://127.0.0.1:11434/api/tags"))
    payload={"model":live.PINNED_MODEL,"input":["search_document: "+d["quote"] for d in live.plan()["documents"]],
             "truncate":False,"keep_alive":"5m","options":{"num_ctx":2048}}
    payload[key]=value
    with pytest.raises(live.StopRun): budget.reserve(httpx.Request("POST","http://127.0.0.1:11434/api/embed",json=payload))
    assert budget.counts=={"embedding_posts":0,"metadata_gets":1}


def test_guard_forbids_historical_databases_and_query_writes(sandbox):
    root,_=sandbox; output=root/"fence-output"; output.mkdir(); (output/"responses").mkdir()
    budget=live.Budget(); budget.begin("ingestion")
    database=root/"fence.sqlite3"; journal=live.Journal(output/"journal.jsonl")
    with live.guarded_access(database,budget,journal,output,lambda:httpx.MockTransport(lambda _:None)):
        with pytest.raises(live.StopRun): sqlite3.connect(root/".local/historical.sqlite3")
        with sqlite3.connect(database) as db: db.execute("CREATE TABLE allowed(value)")
        budget.stage="supported"
        with pytest.raises(live.StopRun): sqlite3.connect(database)
        with pytest.raises(live.StopRun): sqlite3.connect(database.as_uri()+"?mode=rw&unused=mode=ro",uri=True)
        with pytest.raises(live.StopRun): httpx.AsyncClient(trust_env=True,follow_redirects=False)
        with sqlite3.connect(database.as_uri()+"?mode=ro",uri=True) as db:
            with pytest.raises(sqlite3.OperationalError): db.execute("DROP TABLE allowed")


def test_fixed_paths_frozen_plan_and_historical_mutations_fail_closed(sandbox):
    root,_=sandbox
    frozen=root/live.PREPARATION/"plan.json"; original=frozen.read_bytes(); frozen.write_bytes(original+b" ")
    with pytest.raises(live.StopRun,match="frozen"): live.plan()
    frozen.write_bytes(original)
    (root/"labels-v2.json").write_bytes(b"altered")
    with pytest.raises(live.StopRun,match="historical"): live.preservation(require_local=True)


def test_preflight_source_and_provenance_fail_before_claim(sandbox,monkeypatch):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    root,_=sandbox; original=live.git
    monkeypatch.setattr(live,"git",lambda *args:b"dirty" if args[0]=="status" else original(*args))
    with pytest.raises(live.StopRun,match="not_clean"): live.preflight()
    monkeypatch.setattr(live,"git",lambda *args:b"support_agent/answer.py\n" if args[0]=="diff" else original(*args))
    with pytest.raises(live.StopRun,match="unexpected_source"): live.preflight()
    monkeypatch.setattr(live,"git",original)
    transport,_=fake_transport(root); value=live.preflight(); value["run_id"]="historical"
    with pytest.raises(live.StopRun,match="provenance"): live.execute(value,live.Budget(),transport_factory=transport,hard_deadline=False)
    assert not any(p.exists() for p in live.unused_paths().values())


def test_no_live_transport_or_dynamic_cli_overrides_in_offline_tests(sandbox):
    with pytest.raises(live.StopRun,match="fake_transport"): live.execute({},live.Budget())
    for args in ([],["--run-id","other"],["--database","historical.sqlite3"],["--resume"],["--retry"],
                 ["--check-preparation","--execute-authorized-once"]):
        with pytest.raises(SystemExit): live.main(args)


def test_fake_run_installs_and_cancels_hard_deadline(sandbox,monkeypatch):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    root,_=sandbox; transport,_=fake_transport(root); timers=[]
    class Timer:
        def __init__(self,seconds,callback):
            assert 0<seconds<=600 and callable(callback)
            self.started=False; self.cancelled=False; timers.append(self)
        def start(self): self.started=True
        def cancel(self): self.cancelled=True
    monkeypatch.setattr(live.threading,"Timer",Timer)
    report=live.execute(live.preflight(),live.Budget(),transport_factory=transport)
    assert report["status"]=="PASS" and len(timers)==1 and timers[0].started and timers[0].cancelled


@pytest.mark.parametrize("mode",["altered-quote","extra-claim","supported-abstention","unsupported-positive","unsupported-tool-error"])
def test_runner_checks_fixed_outcomes_independently_of_workflow_result(sandbox,monkeypatch,mode):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    from support_agent.investigation import workflow
    original=workflow.investigate
    async def altered(request,runtime):
        value=await original(request,runtime)
        supported=request["question"]==live.plan()["questions"][0]["question"]
        if supported and mode=="altered-quote": value["citations"][0]["quote"]="Fabricated duration."
        if supported and mode=="extra-claim": value["citations"]*=2
        if supported and mode=="supported-abstention": value["abstained"]=True
        if not supported and mode=="unsupported-positive": value["abstained"]=False
        if not supported and mode=="unsupported-tool-error": value["diagnostics"]["reason"]="tool_failed"
        return value
    monkeypatch.setattr(workflow,"investigate",altered)
    root,_=sandbox; transport,calls=fake_transport(root)
    report=live.execute(live.preflight(),live.Budget(),transport_factory=transport,hard_deadline=False)
    assert report["status"]=="STOPPED" and report["preservation"]=="PASS"
    assert len(calls)==(9 if mode.startswith("unsupported") else 6)
    assert report["failure"]["reason"].startswith(("citation_","question_","unsafe_or_unexpected"))
    assert (root/".local"/(live.RUN_ID+".claim.json")).is_file()


def test_sdk_mismatch_and_frozen_history_absence_stop_preflight(sandbox,monkeypatch):
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    import importlib.metadata
    monkeypatch.setattr(importlib.metadata,"version",lambda _:"unexpected")
    with pytest.raises(live.StopRun,match="sdk_identity"): live.preflight()
    root,_=sandbox
    (root/".local/old-failed.sqlite3").write_bytes(b"unapproved")
    with pytest.raises(live.StopRun,match="historical_absence"): live.preservation()
    assert not any(p.exists() for p in live.unused_paths().values())
