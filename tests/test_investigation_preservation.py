"""No optional SDK needed for byte-only historical preservation checks."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from evaluation import investigation_offline as offline


def test_all_available_predecessor_evidence_claims_and_indexes_are_unchanged():
    result=offline.preserved()
    assert result["protected_files"]==293
    assert result["verified_available"]+len(result["skipped_optional"])==293
    if not result["skipped_optional"]:
        assert offline.preserved(require_local=True)["verified_available"]==293
    report=json.loads(Path("reports/lifecycle-real-20261117-once/report.json").read_text())
    assert report["status"]=="PASS" and report["request_counts"]=={"embedding_posts":3,"metadata_gets":6}


def test_preservation_detects_changes_and_missing_claims(tmp_path,monkeypatch):
    target=tmp_path/"consumed.claim.json"; target.write_bytes(b"consumed")
    snapshot={"files":{"consumed.claim.json":offline.sha(target)},"git_representations":{},"optional_in_fresh_ci":[],"required_absent":["old-failed.sqlite3"]}
    manifest=tmp_path/"snapshot.json"; manifest.write_text(json.dumps(snapshot))
    monkeypatch.setattr(offline,"ROOT",tmp_path); monkeypatch.setattr(offline,"SNAPSHOT",manifest)
    monkeypatch.setattr(offline,"SNAPSHOT_SHA256",offline.sha(manifest))
    assert offline.preserved()["verified_available"]==1
    target.write_bytes(b"changed")
    with pytest.raises(ValueError,match="Historical"): offline.preserved()
    target.unlink()
    with pytest.raises(FileNotFoundError): offline.preserved()


def test_demonstrations_are_read_only_and_clearly_synthetic_if_sdk_installed():
    pytest.importorskip("langgraph.graph"); pytest.importorskip("mcp")
    result=offline.demonstrations()
    assert result["actual_model_requests"]==0
    assert len(result["records"])==4
    assert all(r["database_unchanged"] and r["result"]["abstained"]==r["expected_abstention"] for r in result["records"])
    assert "not human-validated" in result["kind"]
