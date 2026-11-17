"""Fixed fresh-run preparation; default/check mode never consumes a claim or calls a model.

Live execution needs separate explicit owner authorization. The CLI flag is an
execution guard, not evidence that approval has been granted. No automatic IDs,
fallback, resume or retry exist. The consumed predecessor is never changed.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import threading

from evaluation import lifecycle_live as verified
from support_agent.documents import local_path

ROOT=Path(__file__).resolve().parents[1]
BASELINE="a90b553394a5d5312734eac16346c1ac603a224b"
RUN_ID="lifecycle-real-20261117-once"
ENGINE_SHA256="1f3a68e97dc784d2a5211657e002acb72bd62c8524b5c1a4801f601ba897dcdc"
PRESERVATION_SHA256="9a37569d38fdb00ebdfdfd468dc5f4c50422ddc39701e5677987775ef267817e"
PLAN_SHA256="0aeca1c4ac2aabebf2f5d2d64ca8a4ed34661a446e61260b3bf8ebf54a17c5d3"
PREPARATION=Path("reports/lifecycle-fresh-preparation")
PROOF=Path(".local/lifecycle-fresh-offline-proof.json")
TEST_LOG=Path(".local/lifecycle-fresh-offline-tests.log")
ALLOWED_NEW={"evaluation/lifecycle_live_fresh.py","tests/test_lifecycle_live_fresh.py","docs/lifecycle-fresh-preparation.md",
             *[(PREPARATION/name).as_posix() for name in ("plan.json","preservation.json","offline.json","report.md")]}
StopRun=verified.StopRun


def _frozen(name,expected):
    path=ROOT/PREPARATION/name
    if verified.sha(path)!=expected: raise StopRun("frozen_preparation_changed:"+name)
    return json.loads(path.read_text(encoding="utf-8"))


def frozen_plan():
    plan=_frozen("plan.json",PLAN_SHA256)
    if plan["run_id"]!=RUN_ID or plan["preparation_baseline"]!=BASELINE or plan["verified_runner_sha256"]!=ENGINE_SHA256:
        raise StopRun("fixed_run_configuration_mismatch")
    return plan


def fixed_paths():
    plan=frozen_plan()
    expected={"workspace":f".local/{RUN_ID}","database":f".local/{RUN_ID}/fictional.sqlite3",
              "claim":f".local/{RUN_ID}.claim.json","output":f"reports/{RUN_ID}"}
    if any(plan[k]!=v for k,v in expected.items()): raise StopRun("fixed_path_mismatch")
    return {k:ROOT/v for k,v in expected.items()}


def unused_paths():
    paths=fixed_paths()
    for parent in (ROOT/".local",ROOT/"reports"):
        # A clean CI checkout has no ignored .local directory. Readiness/path
        # inspection must not create it; execution still requires all old local
        # claims through strict preservation before reaching claim creation.
        local_path(parent if parent.exists() or parent.is_symlink() else parent.parent)
    if any(p.exists() or p.is_symlink() for p in paths.values()):
        raise StopRun("fresh_run_claim_workspace_or_output_already_exists")
    return paths


def preservation(require_local=False):
    snapshot=_frozen("preservation.json",PRESERVATION_SHA256)
    checked={}; skipped=[]
    for name,expected in snapshot["files"].items():
        path=ROOT/name
        if not path.exists() and not require_local and name in snapshot["optional_in_fresh_ci"]:
            skipped.append(name); continue
        actual=verified.sha(path)
        if actual not in {expected,snapshot["git_representations"].get(name,expected)}:
            raise StopRun("previous_artifact_or_claim_changed:"+name)
        checked[name]=actual
    for name in snapshot["required_absent"]:
        if (ROOT/name).exists() or (ROOT/name).is_symlink(): raise StopRun("previous_run_workspace_changed:"+name)
    old_report=json.loads((ROOT/"reports/lifecycle-real-once/report.json").read_text())
    old_claim=json.loads((ROOT/"reports/lifecycle-real-once/run-claim.json").read_text())
    if old_report["status"]!="STOPPED" or old_report["request_counts"]!={"embedding_posts":0,"metadata_gets":1} or old_claim["consumed"] is not True or old_claim["run_id"]!="lifecycle-real-once":
        raise StopRun("previous_run_state_mismatch")
    local_claim=ROOT/".local/lifecycle-real-once.claim.json"
    if local_claim.exists() and verified.sha(local_claim)!=verified.sha(ROOT/"reports/lifecycle-real-once/run-claim.json"):
        raise StopRun("previous_claim_copy_mismatch")
    return {"checked":checked,"skipped_optional":skipped,"previous_run":"consumed STOPPED; unchanged"}


def git(*args):
    return subprocess.check_output(["git","-c","safe.directory="+ROOT.as_posix(),*args],cwd=ROOT,timeout=10)


def proof_sources():
    return {name:verified.sha(ROOT/name) for name in (
        "evaluation/lifecycle_live_fresh.py","tests/test_lifecycle_live_fresh.py","evaluation/lifecycle_live.py",
        (PREPARATION/"plan.json").as_posix(),(PREPARATION/"preservation.json").as_posix())}


def record_offline_proof(exit_code):
    """Offline helper, called only after the shell actually runs pytest successfully."""
    if exit_code!=0: raise StopRun("offline_tests_failed")
    text=(ROOT/TEST_LOG).read_text(encoding="utf-8-sig")
    if not re.search(r"(?m)^\d+ passed(?:,| in )",text) or re.search(r"(?m)^.*\d+ failed",text):
        raise StopRun("successful_offline_log_required")
    proof={"commit":git("rev-parse","HEAD").decode().strip(),"exit_code":0,"command":"python -m pytest -q",
           "source_sha256":proof_sources(),"log_sha256":verified.sha(ROOT/TEST_LOG),"recorded_at_utc":verified.utc()}
    # A proof is replaceable test evidence, never a live claim. New source/commit
    # requires a new successful test run; no live workspace is created here.
    temp=ROOT/PROOF.with_suffix(".tmp")
    with temp.open("wb") as stream:
        stream.write(verified.encoded(proof)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp,ROOT/PROOF)
    return proof


def preflight():
    if git("status","--porcelain").strip(): raise StopRun("working_tree_not_clean")
    head=git("rev-parse","HEAD").decode().strip()
    git("merge-base","--is-ancestor",BASELINE,head)
    changed=set(git("diff","--name-only",BASELINE,head).decode().splitlines())
    if changed-ALLOWED_NEW: raise StopRun("approved_source_or_previous_evidence_changed")
    if verified.sha(Path(verified.__file__))!=ENGINE_SHA256: raise StopRun("verified_runner_changed")
    paths=unused_paths()
    history=preservation(require_local=True)
    proof=json.loads((ROOT/PROOF).read_text(encoding="utf-8"))
    if proof.get("commit")!=head or proof.get("exit_code")!=0 or proof.get("source_sha256")!=proof_sources() or proof.get("log_sha256")!=verified.sha(ROOT/TEST_LOG):
        raise StopRun("offline_preparation_proof_missing_or_stale")
    return {"preparation_baseline":BASELINE,"approved_source_commit":verified.BASELINE,"runner_commit":head,"run_id":RUN_ID,
            "paths":{k:str(v) for k,v in paths.items()},"offline_proof":proof,"original_artifacts":history["checked"],
            "previous_run":history["previous_run"],"plan_sha256":PLAN_SHA256,"preservation_sha256":PRESERVATION_SHA256,
            "owner_observations":"Owner-reported only; no out-of-budget model inspection performed"}


def _engine():
    # Load the exact trusted runner in a private module namespace. The historical
    # module/global RUN_ID and its file remain unchanged, even if execution fails.
    source=Path(verified.__file__)
    if verified.sha(source)!=ENGINE_SHA256: raise StopRun("verified_runner_changed")
    spec=importlib.util.spec_from_file_location("_project5_fixed_fresh_lifecycle_engine",source)
    engine=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    engine.ROOT=ROOT
    engine.RUN_ID=RUN_ID
    return engine


def execute(provenance,budget,*,transport_factory=None,hard_deadline=True):
    if os.environ.get("PYTEST_CURRENT_TEST") and transport_factory is None:
        raise StopRun("offline_tests_require_an_explicit_fake_transport")
    paths=unused_paths()
    if provenance.get("run_id")!=RUN_ID or provenance.get("preparation_baseline")!=BASELINE or provenance.get("paths")!={k:str(v) for k,v in paths.items()}:
        raise StopRun("execution_provenance_mismatch")
    history=preservation(require_local=transport_factory is None)
    if provenance.get("original_artifacts")!=history["checked"]:
        raise StopRun("preservation_snapshot_changed_before_execution")
    engine=_engine()
    report=engine.run(provenance,budget,transport_factory=transport_factory,hard_deadline=hard_deadline)
    try:
        after=preservation(require_local=transport_factory is None)
        if after["checked"]!=history["checked"]: raise StopRun("previous_history_changed_after_execution")
        if not paths["claim"].is_file() or verified.sha(paths["claim"])!=verified.sha(paths["output"]/"run-claim.json"):
            raise StopRun("fresh_claim_missing_or_altered")
        budget.remaining()
    except BaseException as exc:
        verified.save_new(paths["output"]/"fresh-postflight.json",{"status":"STOPPED","reason":str(exc),"checked_at_utc":verified.utc()})
        raise
    verified.save_new(paths["output"]/"fresh-postflight.json",{"status":"PASS","previous_artifacts_preserved":len(after["checked"]),
                      "previous_claims_retained":True,"fresh_claim_consumed":True,"checked_at_utc":verified.utc()})
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-preparation",action="store_true",help="Read-only readiness check: no model calls, no claim/workspace creation")
    modes.add_argument("--execute-authorized-once",action="store_true",help="ONLY after separate explicit owner approval; consumes the fixed new claim")
    args=parser.parse_args(argv)
    # This timer includes preflight and is passed to the verified hard-deadline
    # engine. Check-only mode neither starts a timer thread nor invokes the engine.
    budget=verified.Budget() if args.execute_authorized_once else None
    if args.check_preparation:
        provenance=preflight()
        print(json.dumps({"status":"READY FOR SEPARATE OWNER AUTHORIZATION","run_id":RUN_ID,
                          "commit":provenance["runner_commit"],"protected_artifacts":len(provenance["original_artifacts"]),
                          "paths":provenance["paths"],"model_requests":0,"new_claim_consumed":False},indent=2))
        return
    timer=threading.Timer(budget.remaining(),lambda:os._exit(124))
    timer.daemon=True; timer.start()
    try:
        report=execute(preflight(),budget)
        budget.remaining()
    finally:
        timer.cancel()
    print(json.dumps({k:report.get(k) for k in ("status","request_counts","seconds","failure")},indent=2))
    if report["status"]!="PASS": raise SystemExit(1)


if __name__=="__main__": main()
