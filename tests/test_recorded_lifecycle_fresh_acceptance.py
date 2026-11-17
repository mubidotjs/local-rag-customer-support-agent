"""Read the immutable real acceptance evidence offline; never execute its runner."""
import json
import math
from pathlib import Path
from evaluation.lifecycle_live import sha, PINNED_DIGEST, PINNED_MODEL
from support_agent.text import HEADER, normalize, sentence_spans

OUT=Path("reports/lifecycle-real-20261117-once")
STAGES=("initial","add","content-update","metadata-update","no-op-reindex","delete")


def load(name):
    return json.loads((OUT/name).read_text(encoding="utf-8"))


def test_live_execution_has_the_exact_identity_budget_claim_and_manifest():
    assert sha(OUT/"artifacts.sha256.json")=="a57210f0b623e259e53abc1e0ad583c6bff5006ea571d8de40f32b5bc048f359"
    assert sha(OUT/"report.json")=="8fbc38608525704cc69b96740fb38617b283b6e0e776a7568f07efcc02248e0f"
    assert sha(OUT/"fresh-postflight.json")=="be4944871c951a0bad36ee9a7c8bc829d3e2cfa2c5e71da0023f0f649fd6c725"
    manifest=load("artifacts.sha256.json")
    assert len(manifest)==40 and all(sha(OUT/name)==digest for name,digest in manifest.items())
    report=load("report.json")
    assert report["status"]=="PASS"
    assert report["runner_commit"]=="1518d774e65dfc9c34739b5778546f10a0f46b0b"
    assert report["approved_source_commit"]=="9c3bdb0879b247969a9bc67cda044ad095b97a07"
    assert report["required_digest"]==PINNED_DIGEST and report["model"]==PINNED_MODEL
    assert report["request_counts"]=={"embedding_posts":3,"metadata_gets":6}
    assert report["query_embeddings"]==report["generation_posts"]==report["retries"]==report["downloads"]==report["substitutions"]==0
    assert report["seconds"]<600
    events=[json.loads(s) for s in (OUT/"requests.jsonl").read_text().splitlines()]
    attempts=[e for e in events if e["event"]=="request_attempt"]
    finished=[e for e in events if e["event"]=="request_finished"]
    assert len(attempts)==len(finished)==9
    assert [e["sequence"] for e in attempts]==list(range(1,10))
    assert [(e["method"],e["endpoint"]) for e in attempts]==[("GET","/api/tags"),("POST","/api/embed"),("GET","/api/tags")]*3
    assert all(e["outcome"]=="verified_response" and e["status_code"]==200 for e in finished)
    assert all(e["verified_identity"]["digest"]==PINNED_DIGEST for e in finished if e["kind"]=="metadata_gets")
    assert [e["embedding_count"] for e in finished if e["kind"]=="embedding_posts"]==[2,1,1]
    assert all(e["dimension"]==768 for e in finished if e["kind"]=="embedding_posts")
    allowed=load("preflight.json")["paths"]["database"]
    assert {e["database"] for e in events if e["event"]=="database_open"}=={allowed}
    for sequence in range(1,10):
        req=load(f"requests/{sequence:03d}.json")
        if req["method"]=="POST":
            assert req["payload"]["model"]==PINNED_MODEL
            assert all(t.startswith("search_document: ") for t in req["payload"]["input"])
            body=load(f"responses/{sequence:03d}.body")
            assert all(len(v)==768 and all(math.isfinite(x) for x in v) and any(x!=0 for x in v) for v in body["embeddings"])
    assert load("run-claim.json")["consumed"] is True
    assert load("fresh-postflight.json")["previous_artifacts_preserved"]==233


def test_recorded_vectors_offsets_noop_and_deletion_are_grounded_in_sources():
    report=load("report.json")
    assert [r["operation"] for r in report["operations"]]==list(STAGES)
    assert all(r["status"]=="PASS" for r in report["operations"])
    assert [r["vectors_reused"] for r in report["operations"]]==[0,2,2,3,3,2]
    assert [r["vectors_generated"] for r in report["operations"]]==[2,1,1,0,0,0]
    assert all(r["request_delta"]=={"embedding_posts":0,"metadata_gets":0} for r in report["operations"][3:])
    snapshots={stage:load("snapshots/"+stage+".json") for stage in STAGES}
    for snap in snapshots.values():
        assert snap["dimension"]==768 and snap["metadata"]["embed_digest"]==PINNED_DIGEST
        assert snap["source_offsets_verified"] and snap["fts5_verified"] and snap["sqlite_integrity_verified"]
        docs={d["id"]:d for d in snap["documents"]}
        for p in snap["chunks"]:
            d=docs[p["document_id"]]
            normalized,_=normalize(d["source"])
            assert normalized[p["normalized_start"]:p["normalized_end"]]==p["text"]
            assert normalize(d["source"][p["source_start"]:p["source_end"]])[0]==p["text"]
            body_start=HEADER.match(normalized).end()
            assert (p["normalized_start"]-body_start,p["normalized_end"]-body_start,p["text"]) in list(sentence_spans(normalized[body_start:]))
    def chunk(stage,identifier):
        return next(p for p in snapshots[stage]["chunks"] if p["document_id"]==identifier)
    for identifier in ("selene","torin"):
        assert all(chunk(stage,identifier)==chunk("initial",identifier) for stage in STAGES)
    added=chunk("add","wren"); updated=chunk("content-update","wren"); metadata=chunk("metadata-update","wren")
    assert added["embedding_sha256"]!=updated["embedding_sha256"]
    assert updated["embedding_sha256"]==metadata["embedding_sha256"]
    assert (updated["source_start"],updated["source_end"])!=(metadata["source_start"],metadata["source_end"])
    assert snapshots["metadata-update"]==snapshots["no-op-reindex"]
    assert {p["document_id"] for p in snapshots["delete"]["chunks"]}=={"selene","torin"}
    assert snapshots["delete"]["semantic_candidate_ids"]==["selene:c000","torin:c000"]
    assert report["stale_preview_negative_check"].startswith("PASS")
    assert report["deleted_document_absent_from_sql_fts_and_semantic_candidates"] is True
    assert report["final_index_sha256"]=="081adff9cb8c20f5584f5fdad7744005e171e2a22f8e3148639771642962fa8b"


def test_reviewed_publication_audit_has_no_extra_live_requests():
    audit=load("publication-audit.json")
    assert audit["historical_files_verified"]==233
    assert audit["claim_matches_copy"] is True and audit["post_execution_model_requests"]==0
    assert audit["final_index_sha256"]==load("report.json")["final_index_sha256"]
    assert audit["application_and_runner_source_unchanged"] is True
    manifest=load("publication.sha256.json")
    assert all(sha(OUT/name)==digest for name,digest in manifest.items())
