"""Fresh-run preparation: only fake HTTP transports and temporary fictional indexes."""
import hashlib
import json
from pathlib import Path
import shutil
import httpx
import pytest
from evaluation import lifecycle_live as original
from evaluation import lifecycle_live_fresh as fresh

REAL_ROOT=Path(__file__).resolve().parents[1]


def test_real_history_is_preserved_and_published_run_cannot_be_reused():
    history=fresh.preservation()
    assert len(history["checked"])+len(history["skipped_optional"])==233
    assert history["previous_run"]=="consumed STOPPED; unchanged"
    if not history["skipped_optional"]:
        assert len(fresh.preservation(require_local=True)["checked"])==233
    assert original.RUN_ID=="lifecycle-real-once"
    assert original.sha(Path(original.__file__))==fresh.ENGINE_SHA256
    paths=fresh.fixed_paths()
    if (paths["output"]/"report.json").exists():
        # After the one authorized execution, the published consumed state must
        # block reuse even in CI where ignored local workspace files are absent.
        report=json.loads((paths["output"]/"report.json").read_text())
        claim=json.loads((paths["output"]/"run-claim.json").read_text())
        assert report["run_id"]==fresh.RUN_ID and report["status"] in {"PASS","STOPPED"}
        assert claim["consumed"] is True
        with pytest.raises(fresh.StopRun,match="already_exists"):
            fresh.unused_paths()
    else:
        assert all(not p.exists() for p in fresh.unused_paths().values())


def test_plan_retains_the_exact_model_and_request_ceiling():
    plan=fresh.frozen_plan()
    assert plan["status"].startswith("OFFLINE PREPARATION ONLY")
    assert plan["model"]==original.PINNED_MODEL and plan["digest"]==original.PINNED_DIGEST
    assert (plan["max_embedding_posts"],plan["max_metadata_gets"],plan["max_wall_clock_seconds"])==(3,6,600)
    assert sum(s["embedding_posts"] for s in plan["sequence"])==3
    assert sum(s["metadata_gets"] for s in plan["sequence"])==6
    assert all(s["embedding_posts"]==s["metadata_gets"]==0 for s in plan["sequence"][3:])
    for name in ("generation_posts","query_embeddings","retries","downloads","substitutions","cloud_calls"):
        assert plan[name]==0
    assert "Owner reported" in plan["owner_observations"]["provenance"]


def test_unused_path_inspection_does_not_create_local_in_a_clean_checkout(tmp_path,monkeypatch):
    (tmp_path/"reports").mkdir()
    prep=tmp_path/fresh.PREPARATION; prep.mkdir()
    shutil.copyfile(REAL_ROOT/fresh.PREPARATION/"plan.json",prep/"plan.json")
    monkeypatch.setattr(fresh,"ROOT",tmp_path)
    assert all(not p.exists() for p in fresh.unused_paths().values())
    assert not (tmp_path/".local").exists()


@pytest.fixture
def sandbox(tmp_path,monkeypatch):
    (tmp_path/".local").mkdir()
    (tmp_path/"reports").mkdir()
    names=["reports/lifecycle-real-once/report.json","reports/lifecycle-real-once/run-claim.json","reports/lifecycle-real-once/requests.jsonl"]
    for name in names:
        target=tmp_path/name; target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(REAL_ROOT/name,target)
    old_claim=tmp_path/".local/lifecycle-real-once.claim.json"
    shutil.copyfile(tmp_path/"reports/lifecycle-real-once/run-claim.json",old_claim)
    phase_claim=tmp_path/".local/phase-d-v2-once.claim.json"
    phase_claim.write_text('{"run_id":"phase-d-v2-once","consumed":true,"kind":"synthetic history fixture"}\n')
    frozen=tmp_path/"fictional-frozen-labels.json"
    frozen.write_text('{"kind":"synthetic frozen label fixture"}\n')
    names+=['.local/lifecycle-real-once.claim.json','.local/phase-d-v2-once.claim.json','fictional-frozen-labels.json']
    prep=tmp_path/fresh.PREPARATION; prep.mkdir()
    shutil.copyfile(REAL_ROOT/fresh.PREPARATION/"plan.json",prep/"plan.json")
    original_bytes={n:(tmp_path/n).read_bytes() for n in names}
    snapshot={"baseline_commit":fresh.BASELINE,"files":{n:original.sha(tmp_path/n) for n in names},
              "git_representations":{},"optional_in_fresh_ci":[],"required_absent":[".local/lifecycle-real-once/fictional.sqlite3"]}
    original.save_new(prep/"preservation.json",snapshot)
    for name in ("evaluation/lifecycle_live_fresh.py","evaluation/lifecycle_live.py","tests/test_lifecycle_live_fresh.py"):
        target=tmp_path/name; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(REAL_ROOT/name,target)
    monkeypatch.setattr(fresh,"ROOT",tmp_path)
    monkeypatch.setattr(fresh,"PRESERVATION_SHA256",original.sha(prep/"preservation.json"))
    def git(*args):
        if args[:2]==("status","--porcelain"): return b""
        if args[0]=="rev-parse": return b"synthetic-offline-preparation-commit"
        if args[0]=="merge-base": return b""
        if args[0]=="diff": return b"evaluation/lifecycle_live_fresh.py\n"
        raise AssertionError(args)
    monkeypatch.setattr(fresh,"git",git)
    (tmp_path/fresh.TEST_LOG).write_text("12 passed, 1 skipped in 0.1s\n",encoding="utf-8")
    fresh.record_offline_proof(0)
    return tmp_path,original_bytes


def fake_transport(root,mode="valid"):
    events=[]
    def respond(request):
        events.append((request.method,request.url.path))
        ledger=[json.loads(s) for s in (root/"reports"/fresh.RUN_ID/"requests.jsonl").read_text().splitlines()]
        assert len([e for e in ledger if e["event"]=="request_attempt"])==len(events)
        if mode=="connection": raise httpx.ConnectError("fake offline connection failure",request=request)
        if request.method=="GET":
            digest="0"*64 if mode=="identity" or mode=="after-post" and len(events)==3 else original.PINNED_DIGEST
            return httpx.Response(200,json={"models":[{"name":original.PINNED_MODEL,"digest":digest}]})
        data=json.loads(request.content)
        vectors=[[float(n+1) for n in hashlib.sha256(t.encode()).digest()]+[0.0]*736 for t in data["input"]]
        return httpx.Response(200,json={"model":original.PINNED_MODEL,"embeddings":vectors})
    return lambda:httpx.MockTransport(respond),events


def assert_history(sandbox):
    root,original_bytes=sandbox
    assert all((root/name).read_bytes()==data for name,data in original_bytes.items())
    assert original.RUN_ID=="lifecycle-real-once"


def test_readiness_check_and_offline_proof_never_create_live_paths(sandbox,capsys):
    root,_=sandbox
    fresh.main(["--check-preparation"])
    result=json.loads(capsys.readouterr().out)
    assert result["model_requests"]==0 and result["new_claim_consumed"] is False
    assert result["status"]=="READY FOR SEPARATE OWNER AUTHORIZATION"
    assert all(not p.exists() for p in fresh.fixed_paths().values())
    assert_history(sandbox)


def test_full_fake_fresh_run_preserves_old_claims_and_cannot_be_reused(sandbox):
    root,_=sandbox
    provenance=fresh.preflight()
    transport,events=fake_transport(root)
    report=fresh.execute(provenance,original.Budget(),transport_factory=transport,hard_deadline=False)
    assert report["status"]=="PASS",report
    assert report["run_id"]==fresh.RUN_ID
    assert report["request_counts"]=={"embedding_posts":3,"metadata_gets":6}
    assert [s["vectors_reused"] for s in report["operations"]]==[0,2,2,3,3,2]
    assert [s["vectors_generated"] for s in report["operations"]]==[2,1,1,0,0,0]
    assert all(s["request_delta"]=={"embedding_posts":0,"metadata_gets":0} for s in report["operations"][3:])
    assert report["stale_preview_negative_check"].startswith("PASS")
    assert report["deleted_document_absent_from_sql_fts_and_semantic_candidates"]
    assert len(events)==9
    paths=fresh.fixed_paths(); claim=paths["claim"].read_bytes()
    audit=json.loads((paths["output"]/"fresh-postflight.json").read_text())
    assert audit["previous_claims_retained"] and audit["fresh_claim_consumed"]
    with pytest.raises(fresh.StopRun,match="already_exists"):
        fresh.execute(provenance,original.Budget(),transport_factory=transport,hard_deadline=False)
    assert paths["claim"].read_bytes()==claim and len(events)==9
    # The historical runner remains locked to its consumed ID. No global
    # retargeting occurs when loading the separate verified-engine namespace.
    engine=fresh._engine(); engine.RUN_ID="lifecycle-real-once"
    with pytest.raises(FileExistsError):
        engine.run(provenance,original.Budget(),transport_factory=transport,hard_deadline=False)
    assert_history(sandbox)


@pytest.mark.parametrize("mode,posts,gets",[("connection",0,1),("identity",0,1),("after-post",1,2)])
def test_fake_failure_consumes_only_the_new_temporary_claim_and_stops(sandbox,mode,posts,gets):
    root,_=sandbox
    transport,events=fake_transport(root,mode)
    report=fresh.execute(fresh.preflight(),original.Budget(),transport_factory=transport,hard_deadline=False)
    assert report["status"]=="STOPPED" and not report["operations"]
    assert report["request_counts"]=={"embedding_posts":posts,"metadata_gets":gets}
    assert len(events)==posts+gets
    assert fresh.fixed_paths()["claim"].exists()
    assert_history(sandbox)


@pytest.mark.parametrize("key",["claim","workspace","output"])
def test_existing_fresh_path_is_not_deleted_renamed_or_bypassed(sandbox,key):
    path=fresh.fixed_paths()[key]
    if key=="claim": path.write_bytes(b"already claimed")
    else: path.mkdir()
    with pytest.raises(fresh.StopRun,match="already_exists"): fresh.preflight()
    assert path.exists()
    assert_history(sandbox)


@pytest.mark.parametrize("name",[".local/lifecycle-real-once.claim.json",".local/phase-d-v2-once.claim.json","reports/lifecycle-real-once/requests.jsonl","fictional-frozen-labels.json"])
def test_history_deletion_or_edit_is_rejected_before_new_claim(sandbox,name):
    root,_=sandbox
    target=root/name
    target.write_bytes(target.read_bytes()+b" ")
    with pytest.raises(fresh.StopRun,match="previous_artifact_or_claim_changed"):
        fresh.preflight()
    assert not fresh.fixed_paths()["claim"].exists()


def test_missing_previous_claim_is_not_recreated_or_ignored(sandbox):
    root,_=sandbox
    old=root/".local/lifecycle-real-once.claim.json"; old.unlink()
    with pytest.raises(FileNotFoundError): fresh.preflight()
    assert not old.exists() and not fresh.fixed_paths()["claim"].exists()


def test_no_arbitrary_run_id_paths_or_predecessor_retargeting(sandbox,monkeypatch):
    for argv in ([],["--execute-authorized-once","--run-id","lifecycle-real-once"],["--check-preparation","--db","historical.sqlite3"]):
        with pytest.raises(SystemExit): fresh.main(argv)
    monkeypatch.setattr(fresh,"RUN_ID","lifecycle-real-once")
    with pytest.raises(fresh.StopRun,match="configuration_mismatch"): fresh.fixed_paths()
    assert_history(sandbox)


def test_source_or_log_proof_changes_require_new_offline_tests(sandbox):
    root,_=sandbox
    (root/fresh.TEST_LOG).write_text("modified test log")
    with pytest.raises(fresh.StopRun,match="proof_missing_or_stale"): fresh.preflight()
    with pytest.raises(fresh.StopRun,match="failed"): fresh.record_offline_proof(1)
    assert not fresh.fixed_paths()["claim"].exists()


def test_wrong_provenance_and_unsafe_real_transport_are_refused_before_claim(sandbox):
    root,_=sandbox
    provenance=fresh.preflight(); provenance["run_id"]="lifecycle-real-once"
    transport,events=fake_transport(root)
    with pytest.raises(fresh.StopRun,match="provenance_mismatch"):
        fresh.execute(provenance,original.Budget(),transport_factory=transport,hard_deadline=False)
    with pytest.raises(fresh.StopRun,match="explicit_fake_transport"):
        fresh.execute(fresh.preflight(),original.Budget(),hard_deadline=False)
    assert not events and not fresh.fixed_paths()["claim"].exists()
    assert_history(sandbox)


def test_exclusive_claim_creation_handles_a_race_without_fallback(sandbox,monkeypatch):
    root,_=sandbox
    provenance=fresh.preflight(); engine=fresh._engine(); run=engine.run
    def race(*args,**kwargs):
        fresh.fixed_paths()["claim"].write_bytes(b"concurrent claim")
        return run(*args,**kwargs)
    engine.run=race
    monkeypatch.setattr(fresh,"_engine",lambda:engine)
    transport,events=fake_transport(root)
    with pytest.raises(FileExistsError):
        fresh.execute(provenance,original.Budget(),transport_factory=transport,hard_deadline=False)
    assert fresh.fixed_paths()["claim"].read_bytes()==b"concurrent claim"
    assert not events and not fresh.fixed_paths()["workspace"].exists()
    assert_history(sandbox)


def test_verified_budget_and_database_guards_are_reused_unchanged(sandbox):
    engine=fresh._engine()
    assert engine.RUN_ID==fresh.RUN_ID and original.RUN_ID=="lifecycle-real-once"
    assert engine.BASELINE==original.BASELINE
    budget=engine.Budget(clock=lambda:0); budget.begin("initial")
    for endpoint in ("/api/chat","/api/pull","/api/version"):
        with pytest.raises(engine.StopRun):
            budget.reserve(httpx.Request("GET","http://127.0.0.1:11434"+endpoint))
    assert not sum(budget.counts.values())


def test_changes_to_approved_source_are_not_allowed_by_preflight(sandbox,monkeypatch):
    old=fresh.git
    monkeypatch.setattr(fresh,"git",lambda *args:b"support_agent/store.py\n" if args[0]=="diff" else old(*args))
    with pytest.raises(fresh.StopRun,match="approved_source_or_previous_evidence_changed"):
        fresh.preflight()
    assert not fresh.fixed_paths()["claim"].exists()
