"""Validate owner-approved immutable labels without models or index operations."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import pytest
from support_agent.text import HEADER, chunks, normalize


VERSION = Path("evaluation/labels/v2")
LABEL_DIGEST = "b7a4600f21bf2e030390ecb7b749c5c2d9ac6feb9fc826250de7665baa1a22e6"
MANIFEST_DIGEST = "212fc51b361921d63296094d65f0f0c9fa30526fe2d507b3ee211e0fc93018d8"
PROPOSAL_DIGEST = "6e9bf1de19f1c3dec1e44eae730334debafc23d5b6df5d071d32967ddd420578"
SNAPSHOT_DIGEST = "8f226155501e2e305f272eac1cee37e6ee85d9dcc31b5bd192a6e4e0ef4abb7d"
PROPOSAL_COMMIT = "620d970c01232e28a73b72e37385b28c6d90203a"


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_published_v2_bytes_and_package_members_are_pinned():
    assert digest(VERSION / "labels.json") == LABEL_DIGEST
    assert digest(VERSION / "manifest.json") == MANIFEST_DIGEST
    manifest = read_json(VERSION / "manifest.json")
    assert manifest["label_version"] == "v2" and manifest["labels_sha256"] == LABEL_DIGEST
    assert set(p.name for p in VERSION.iterdir()) == set(manifest["files"]) | {"manifest.json"}
    for filename, expected in manifest["files"].items():
        assert digest(VERSION / filename) == expected
        raw = (VERSION / filename).read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n")
    assert (VERSION / "labels.sha256").read_text() == LABEL_DIGEST + "  labels.json\n"


def test_finalized_cases_match_exact_approved_proposal():
    proposal_path = Path("evaluation/proposals/review-v2-proposed.json")
    assert digest(proposal_path) == PROPOSAL_DIGEST
    proposal = read_json(proposal_path)
    finalized = read_json(VERSION / "labels.json")
    assert finalized["golden"] == proposal["candidate_labels"]["golden"]
    expected_holdout = deepcopy(proposal["candidate_labels"]["holdout"])
    expected_holdout.pop("label_status")  # Historical draft metadata, not a case label.
    assert finalized["holdout"] == expected_holdout
    assert len(finalized["golden"]) == len(finalized["holdout"]["cases"]) == 12
    assert finalized["required_source_locations"]["golden/reset"] == proposal["proposed_changes"][0]["source_evidence"]


def test_only_owner_approved_label_differences_from_originals():
    finalized = read_json(VERSION / "labels.json")
    original_golden = read_json("evaluation/golden.json")
    expected_golden = deepcopy(original_golden)
    next(c for c in expected_golden if c["id"] == "reset")["quotes"].append(
        "Release the reset button when the status light flashes amber three times.")
    assert finalized["golden"] == expected_golden
    reset = next(c for c in finalized["golden"] if c["id"] == "reset")
    assert reset["documents"] == ["reset"] and reset["abstain"] is False
    original_holdout = read_json("evaluation/holdout/cases.json")
    expected_holdout = deepcopy(original_holdout)
    expected_holdout.pop("label_status")
    injection = next(c for c in expected_holdout["cases"] if c["id"] == "document-injection")
    changes = read_json("evaluation/proposals/review-v2-proposed.json")["proposed_changes"][1]["fields"]
    for field, change in changes.items():
        assert injection.get(field) == change["before"]
        injection[field] = change["after"]
    assert finalized["holdout"] == expected_holdout
    assert injection["abstain"] is True
    assert injection["classification"] == "security-quarantine"
    assert injection["expected_verification_reason"] == "untrusted_source"


@pytest.mark.parametrize("ordinal,offsets", [(0, (99, 202)), (1, (203, 276))])
def test_reset_exact_source_locations_preserved(ordinal, offsets):
    finalized = read_json(VERSION / "labels.json")
    evidence = finalized["required_source_locations"]["golden/reset"][ordinal]
    source = Path(evidence["file"]).read_bytes().decode("utf-8")
    normalized, mapping = normalize(source)
    assert evidence["document_id"] == "reset" and evidence["version"] == "2.3"
    assert evidence["chunk_id"] == "reset:c000"
    assert evidence["normalized_offsets"] == evidence["source_offsets"] == list(offsets)
    a, b = offsets
    assert normalized[a:b] == evidence["quote"] == source[a:b]
    assert mapping[a][0] == a and mapping[b-1][1] == b
    assert evidence["lines"] == [9+ordinal, 9+ordinal]
    header = HEADER.match(normalized)
    start, end, text = next(chunks(normalized[header.end():]))
    assert start + header.end() <= a < b <= end + header.end()
    assert evidence["quote"] in text


def test_paraphrase_supported_and_historical_failure_remain_visible():
    finalized = read_json(VERSION / "labels.json")
    old = next(c for c in read_json("evaluation/holdout/cases.json")["cases"] if c["id"] == "lexical-paraphrase")
    new = next(c for c in finalized["holdout"]["cases"] if c["id"] == "lexical-paraphrase")
    assert old == new and new["classification"] == "supported" and new["abstain"] is False
    finding = finalized["preserved_finding"]
    report = read_json(finding["historical_report"])
    assert digest(finding["historical_report"]) == finding["historical_report_sha256"]
    assert report["known_false_abstentions"] == ["lexical-paraphrase"]
    assert report["scripted_outcomes_matching_labels"] == finding["historical_scripted_outcomes_matching_labels"] == 11
    assert finding["known_false_abstention"] is True


def test_approval_is_label_finalization_only_and_budget_is_not_executable():
    approval = read_json(VERSION / "approval.json")
    manifest = read_json(VERSION / "manifest.json")
    budget = read_json(VERSION / "execution-budget.json")
    assert approval["owner_approval_received"] is True
    assert approval["approved_proposal_commit"] == PROPOSAL_COMMIT
    assert approval["approved_proposal_sha256"] == PROPOSAL_DIGEST
    assert approval["labels_sha256"] == budget["labels_sha256"] == LABEL_DIGEST
    assert approval["independent_review_of_all_cases_claimed"] is False
    for item in (approval, manifest):
        assert item["approval_scope"] == "label_finalization_only"
        assert item["phase_d_authorized"] is False
        assert item["inference_budget_approved_for_execution"] is False
    assert budget["authorized"] is False and budget["phase_d_authorized"] is False
    proposal = read_json("evaluation/proposals/review-v2-proposed.json")
    for field, expected in proposal["proposed_inference_ceiling"].items():
        assert budget[field] == expected
    old_budget = read_json("evaluation/holdout/proposed-live-budget.json")
    assert budget["models"] == old_budget["models"]
    assert budget["max_answer_generation_requests"] == 12
    assert budget["max_total_embedding_requests"] == 23
    assert budget["wall_clock_minutes"] == 20
    assert budget["automatic_retries"] == budget["downloads"] == budget["substitutions"] == 0
    assert manifest["actual_embedding_requests"] == manifest["actual_generation_requests"] == manifest["model_downloads"] == 0


def test_original_artifacts_and_review_proposal_remain_preserved():
    snapshot_path = Path("evaluation/proposals/review-v2-preservation.json")
    assert digest(snapshot_path) == SNAPSHOT_DIGEST
    snapshot = read_json(snapshot_path)
    optional = set(read_json("evaluation/baseline-manifest.json")["local_artifacts"])
    assert len(snapshot["original_artifact_sha256"]) == 54
    for name, original in snapshot["original_artifact_sha256"].items():
        path = Path(name)
        if name in optional and not path.exists():
            continue  # Existing ignored artifacts are absent from fresh CI checkouts.
        allowed = {original, snapshot["original_git_blob_sha256"].get(name, original)}
        assert digest(path) in allowed, name
