"""Immutable stopped-run evidence: offline file validation, never live execution."""
import json
from pathlib import Path
from evaluation.lifecycle_live import sha


def test_single_real_attempt_stopped_before_embedding_and_is_not_called_acceptance():
    output=Path("reports/lifecycle-real-once")
    assert sha(output/"artifacts.sha256.json")=="bdc5327b1410c1d8e4e51b0a8308606325564bf2da73ed41b642cca079846b9b"
    assert sha(output/"report.json")=="e72638acf97a8432f5bddc95125f84d1d23939392ee570ecad3de4e1a3b93a8a"
    report=json.loads((output/"report.json").read_text())
    assert report["status"]=="STOPPED" and report["operations"]==[]
    assert report["request_counts"]=={"embedding_posts":0,"metadata_gets":1}
    assert report["query_embeddings"]==report["generation_posts"]==report["retries"]==0
    events=[json.loads(s) for s in (output/"requests.jsonl").read_text().splitlines()]
    assert [e["event"] for e in events]==["request_attempt","request_finished","run_stopped"]
    assert events[0]["method"]=="GET" and events[0]["endpoint"]=="/api/tags"
    assert events[1]["error_type"]=="ConnectError" and events[1]["outcome"]=="failed"
    assert report["seconds"]<600
    claim=json.loads((output/"run-claim.json").read_text())
    assert claim["consumed"] is True
    audit=json.loads((output/"publication-audit.json").read_text())
    assert audit["protected_artifacts_verified"]==209 and audit["new_index_exists"] is False
    assert audit["claim_matches_execution_copy"] is True


def test_publication_files_have_a_separate_consistent_manifest():
    output=Path("reports/lifecycle-real-once")
    manifest=json.loads((output/"publication.sha256.json").read_text())
    assert all(sha(output/name)==digest for name,digest in manifest.items())
