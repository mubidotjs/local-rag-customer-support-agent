"""Budget/lifecycle tests use only synthetic transports and temporary databases."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import sys
import pytest
from evaluation import phase_d as runner
from support_agent.config import Settings
from support_agent.ollama import ModelError, Ollama


def embed_payload(query=False):
    return {"model": "nomic-embed-text:latest", "input": ["search_query: test" if query else "search_document: test"]}


def chat_payload():
    return {"model": "qwen3:4b", "messages": []}


def test_budget_accepts_exact_ceiling_and_refuses_any_additional_attempt():
    budget = runner.Budget(clock=lambda: 0)
    for i in range(11):
        budget.reserve("/api/embed", "ingestion", f"s{i}", None, embed_payload())
    for i in range(12):
        budget.reserve("/api/embed", "answering", "s0", f"c{i}", embed_payload(True))
        budget.reserve("/api/chat", "answering", "s0", f"c{i}", chat_payload())
    assert budget.counts == {"ingestion_embedding": 11, "query_embedding": 12, "generation": 12}
    for endpoint, phase, scenario, case, payload in (
        ("/api/embed", "ingestion", "s12", None, embed_payload()),
        ("/api/embed", "answering", "s0", "c12", embed_payload(True)),
        ("/api/chat", "answering", "s0", "c0", chat_payload()),
    ):
        with pytest.raises(runner.StopRun):
            budget.reserve(endpoint, phase, scenario, case, payload)
    assert budget.counts == {"ingestion_embedding": 11, "query_embedding": 12, "generation": 12}


@pytest.mark.parametrize("endpoint,phase,case,payload", [
    ("/api/pull", "ingestion", None, embed_payload()),
    ("/api/embed", "unknown", None, embed_payload()),
    ("/api/chat", "answering", "c1", chat_payload()),
    ("/api/embed", "ingestion", None, {"model": "replacement", "input": ["test"]}),
    ("/api/embed", "ingestion", None, {"model": "nomic-embed-text:latest", "input": ["test"]*17}),
    ("/api/embed", "answering", "c1", {"model": "nomic-embed-text:latest", "input": ["test"]*2}),
])
def test_forbidden_endpoints_stages_models_or_batches_never_consume_a_post(endpoint, phase, case, payload):
    budget = runner.Budget(clock=lambda: 0)
    with pytest.raises(runner.StopRun):
        budget.reserve(endpoint, phase, "s1", case, payload)
    assert sum(budget.counts.values()) == 0


def test_deadline_prevents_post_before_transport(tmp_path, monkeypatch):
    now = [0]
    budget = runner.Budget(clock=lambda: now[0])
    journal = runner.Journal(tmp_path / "ledger.jsonl")
    model = runner.RecordedOllama(Settings(), budget, journal, tmp_path)
    model.phase, model.scenario = "ingestion", "s1"
    now[0] = 1200
    called = []
    monkeypatch.setattr(Ollama, "_post", lambda *args: called.append(True))
    with pytest.raises(runner.StopRun, match="overall_timeout"):
        model._post("/api/embed", embed_payload())
    assert called == [] and sum(budget.counts.values()) == 0
    assert (tmp_path / "ledger.jsonl").read_text() == ""


def test_journal_precedes_send_and_failure_spends_attempt_without_retry(tmp_path, monkeypatch):
    budget = runner.Budget()
    journal = runner.Journal(tmp_path / "ledger.jsonl")
    model = runner.RecordedOllama(Settings(), budget, journal, tmp_path)
    model.phase, model.scenario = "ingestion", "s1"
    called = []
    def broken(self, endpoint, payload):
        events = [json.loads(s) for s in (tmp_path / "ledger.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "post_attempt"
        assert events[-1]["counters"]["ingestion_embedding"] == 1
        assert (tmp_path / events[-1]["request_file"]).exists()
        called.append(endpoint)
        raise ModelError("synthetic transport failure")
    monkeypatch.setattr(Ollama, "_post", broken)
    with pytest.raises(ModelError):
        model._post("/api/embed", embed_payload())
    with pytest.raises(runner.StopRun, match="repeat_attempt"):
        model._post("/api/embed", embed_payload())
    assert called == ["/api/embed"] and budget.counts["ingestion_embedding"] == 1
    assert json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])["outcome"] == "error"


def test_remaining_global_time_bounds_transport_and_post_completion(tmp_path, monkeypatch):
    now = [0]
    budget = runner.Budget(clock=lambda: now[0])
    model = runner.RecordedOllama(Settings(), budget, runner.Journal(tmp_path / "ledger.jsonl"), tmp_path)
    model.phase, model.scenario = "ingestion", "s1"
    now[0] = 1170
    def overrun(self, endpoint, payload):
        assert self.settings.timeout_seconds == 30
        now[0] = 1201
        return {"model": payload["model"], "embeddings": [[1.0, 0.0]]}
    monkeypatch.setattr(Ollama, "_post", overrun)
    with pytest.raises(runner.StopRun, match="overall_timeout"):
        model._post("/api/embed", embed_payload())
    assert budget.counts["ingestion_embedding"] == 1
    assert model.calls[0]["outcome"] == "error"


def test_model_identity_mismatch_and_one_run_claim_are_fail_closed(tmp_path):
    tags = {"models": [{"name": n, "digest": d} for n, d in runner.MODELS.items()]}
    assert runner.verify_identity(tags, {"version": "synthetic"})["models"]
    for key, value in (("digest", "wrong"), ("remote_host", "https://remote.example")):
        changed = deepcopy(tags)
        changed["models"][0][key] = value
        with pytest.raises(runner.StopRun, match="model_mismatch"):
            runner.verify_identity(changed, {"version": "synthetic"})
    claim = tmp_path / "one.claim.json"
    runner.claim_once(claim, {"run": 1})
    before = claim.read_bytes()
    with pytest.raises(runner.StopRun, match="already_claimed"):
        runner.claim_once(claim, {"run": 2})
    assert claim.read_bytes() == before


@pytest.fixture
def synthetic_run(tmp_path, monkeypatch):
    original_root = runner.ROOT
    labels = json.loads((original_root / "evaluation/labels/v2/labels.json").read_text())
    cases = {c["id"]: c for c in labels["holdout"]["cases"]}
    plan = json.loads((original_root / "evaluation/holdout/proposed-live-budget.json").read_text())["chunks_per_scenario"]
    shutil.copytree(original_root / "evaluation/holdout/corpora", tmp_path / "evaluation/holdout/corpora")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "preflight", lambda: (labels, {
        "runner_commit": "synthetic-test-only", "scenarios": plan, "original_artifacts": {"synthetic": "unchanged"}}))
    monkeypatch.setattr(runner, "preserve_originals", lambda **kwargs: {"synthetic": "unchanged"})
    async def identity(*args):
        return {"models": {n: {"digest": d} for n, d in runner.MODELS.items()}, "ollama": {"version": "synthetic-test"}}
    monkeypatch.setattr(runner, "identity_gets", identity)
    def post(self, endpoint, payload):
        if endpoint == "/api/embed":
            return {"model": payload["model"], "embeddings": [[1.0]+[0.0]*767 for _ in payload["input"]]}
        case = cases[self.case_id]
        claims = []
        if not case["abstain"]:
            passages = json.loads(payload["messages"][-1]["content"])["evidence"]
            for quote in dict.fromkeys(e["quote"] for e in case["evidence"]):
                p = next(p for p in passages if quote in p["text"])
                claims.append({"chunk_id": p["chunk_id"], "quote": quote})
        return {"model": payload["model"], "done": True,
                "message": {"content": json.dumps({"abstain": not claims, "claims": claims})}}
    monkeypatch.setattr(Ollama, "_post", post)
    monkeypatch.setattr(sys, "argv", ["phase_d", "--execute-authorized-once"])
    runner.main()
    path = tmp_path / "reports" / runner.RUN_ID / "report.json"
    return json.loads(path.read_text()), tmp_path, cases


def test_full_synthetic_lifecycle_uses_11_fresh_indexes_12_queries_and_preserves_ledger(synthetic_run):
    report, root, _ = synthetic_run
    assert report["status"] == "completed"
    assert len(report["ingestions"]) == 11 and len(report["records"]) == 12
    assert report["request_counts"] == {"ingestion_embedding": 11, "query_embedding": 12, "generation": 8, "total_embedding_posts": 23}
    assert all(r["index_sha256_before"] == r["index_sha256_after"] for r in report["records"])
    # This is an oracle/synthetic lifecycle, not the immutable recorded live run.
    assert report["summary"]["supported_false_abstentions"]["numerator"] == 0
    assert report["summary"]["security_quarantine_cases"][0]["reason"] == "untrusted_source"
    events = [json.loads(s) for s in (root / "reports" / runner.RUN_ID / "requests.jsonl").read_text().splitlines()]
    attempted = [e for e in events if e["event"] == "post_attempt"]
    finished = [e for e in events if e["event"] == "post_finished"]
    assert len(attempted) == len(finished) == 31
    assert [e["sequence"] for e in attempted] == list(range(1, 32))
    with pytest.raises(runner.StopRun, match="already_claimed"):
        runner.main()


def test_exact_source_validity_is_not_semantic_correctness(synthetic_run):
    report, root, cases = synthetic_run
    record = next(r for r in report["records"] if r["id"] == "late-amber")
    result = deepcopy(record["result"])
    expected = {e["quote"] for e in cases["late-amber"]["evidence"]}
    p = next(p for p in result["passages"] if any(q["quote"] not in expected for q in p["quotes"]))
    q = next(q for q in p["quotes"] if q["quote"] not in expected)
    result["citations"].append({"chunk_id": p["id"], "document_id": p["document_id"], "version": p["version"], **q})
    model = type("RecordedStub", (), {"selections": {}, "generation_inputs": {}, "calls": []})()
    settings = replace(Settings(), db_path=root / ".local" / runner.RUN_ID / "indexes/long.sqlite3")
    scored = runner.score_case(cases["late-amber"], result, settings, model, 0, "same", "same")
    assert all(c["exact_source_valid"] for c in scored["citation_checks"])
    assert scored["grounded_answer_correct_under_frozen_rubric"] is False
    assert "extra_or_irrelevant_exact_source_sentence" in scored["failure_categories"]
