"""One authorized, bounded local evaluation. No retries, tuning, or smoke calls.

Default execution is refused. The fixed one-run claim is written before any HTTP.
Ordinary CI imports/tests the guard and scoring functions with synthetic transports.
"""
import argparse
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import threading
import time

import httpx
from support_agent.answer import answer
from support_agent.config import Settings
from support_agent.ollama import ModelError, Ollama
from support_agent.store import connect, ingest
from support_agent.text import HEADER, chunks, has_instructions, normalize

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "a9f30ecd7cc2069e63c048bae6f5a668d8f18a61"
LABEL_DIGEST = "b7a4600f21bf2e030390ecb7b749c5c2d9ac6feb9fc826250de7665baa1a22e6"
MANIFEST_DIGEST = "212fc51b361921d63296094d65f0f0c9fa30526fe2d507b3ee211e0fc93018d8"
RUN_ID = "phase-d-v2-once"
MODELS = {
    "nomic-embed-text:latest": "0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f",
    "qwen3:4b": "359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7",
}


class StopRun(RuntimeError):
    """Fatal run boundary, intentionally NOT caught as an ordinary ModelError."""


def utc():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_bytes(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def save_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())


class Journal:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        with path.open("x", encoding="utf-8"):
            pass

    def write(self, event):
        with self.lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"utc": utc(), **event}, ensure_ascii=False) + "\n")
                stream.flush()
                os.fsync(stream.fileno())


class Budget:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.started = clock()
        self.counts = {"ingestion_embedding": 0, "query_embedding": 0, "generation": 0}
        self.used = {key: set() for key in self.counts}

    def remaining(self):
        remaining = 1200 - (self.clock() - self.started)
        if remaining <= 0:
            raise StopRun("overall_timeout")
        return remaining

    def reserve(self, endpoint, phase, scenario, case_id, payload):
        self.remaining()
        if endpoint == "/api/embed" and phase == "ingestion" and case_id is None:
            kind, key, cap = "ingestion_embedding", scenario, 11
        elif endpoint == "/api/embed" and phase == "answering" and case_id:
            kind, key, cap = "query_embedding", case_id, 12
        elif endpoint == "/api/chat" and phase == "answering" and case_id:
            kind, key, cap = "generation", case_id, 12
            if case_id not in self.used["query_embedding"]:
                raise StopRun("generation_without_query_embedding")
        else:
            raise StopRun("unauthorized_endpoint_or_stage")
        expected_model = "qwen3:4b" if kind == "generation" else "nomic-embed-text:latest"
        if payload.get("model") != expected_model:
            raise StopRun("model_mismatch")
        if key in self.used[kind] or self.counts[kind] >= cap:
            raise StopRun("budget_violation_or_repeat_attempt")
        if kind != "generation":
            inputs = payload.get("input")
            if not isinstance(inputs, list) or not 1 <= len(inputs) <= (16 if kind == "ingestion_embedding" else 1):
                raise StopRun("embedding_batch_violation")
            if self.counts["ingestion_embedding"] + self.counts["query_embedding"] >= 23:
                raise StopRun("embedding_budget_violation")
        self.used[kind].add(key)
        self.counts[kind] += 1
        return kind, sum(self.counts.values())


def verify_identity(tags, version):
    if not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        raise StopRun("invalid_model_identity_response")
    found = {}
    for name, digest in MODELS.items():
        matches = [m for m in tags["models"] if m.get("name") == name]
        if len(matches) != 1 or matches[0].get("digest") != digest or matches[0].get("remote_host"):
            raise StopRun("model_mismatch:" + name)
        found[name] = matches[0]
    if not isinstance(version, dict) or not isinstance(version.get("version"), str):
        raise StopRun("invalid_ollama_version")
    return {"models": found, "ollama": version}


async def identity_gets(budget, journal):
    results = []
    for endpoint in ("/api/tags", "/api/version"):
        timeout = min(5, budget.remaining())
        journal.write({"event": "metadata_get_attempt", "endpoint": endpoint, "timeout_seconds": timeout})
        async with asyncio.timeout(timeout):
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout) as client:
                async with client.stream("GET", "http://127.0.0.1:11434" + endpoint) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for part in response.aiter_bytes():
                        body.extend(part)
                        if len(body) > 2_000_000:
                            raise StopRun("identity_response_too_large")
                    results.append(json.loads(body))
        journal.write({"event": "metadata_get_completed", "endpoint": endpoint})
    return verify_identity(*results)


class RecordedOllama(Ollama):
    def __init__(self, settings, budget, journal, output):
        super().__init__(settings)
        self.budget, self.journal, self.output = budget, journal, output
        self.phase, self.scenario, self.case_id = None, None, None
        self.selections, self.generation_inputs, self.calls = {}, {}, []

    def _post(self, endpoint, payload):
        kind, sequence = self.budget.reserve(endpoint, self.phase, self.scenario, self.case_id, payload)
        if self.settings.ollama_url != "http://127.0.0.1:11434":
            raise StopRun("non_loopback_endpoint")
        request_path = self.output / "requests" / f"{sequence:03d}.json"
        save_new(request_path, payload)
        record = {"sequence": sequence, "kind": kind, "endpoint": endpoint,
                  "scenario": self.scenario, "case_id": self.case_id,
                  "model": payload["model"], "verified_model_digest": MODELS[payload["model"]],
                  "request_sha256": sha(request_path), "request_file": request_path.relative_to(self.output).as_posix(),
                  "attempted_at_utc": utc(), "counters": dict(self.budget.counts)}
        # An attempt is durably journaled BEFORE the sole transport call. A crash or
        # timeout consumes the attempt; there is no retry path or resume mode.
        self.journal.write({"event": "post_attempt", **record})
        self.calls.append(record)
        previous = self.settings
        self.settings = replace(previous, timeout_seconds=min(90, self.budget.remaining()))
        start = time.monotonic()
        try:
            result = super()._post(endpoint, payload)
            record["response_model"] = result.get("model")
            if result.get("model") != payload["model"]:
                raise StopRun("response_model_mismatch")
            response = result if kind == "generation" else {
                key: value for key, value in result.items() if key != "embeddings"}
            if kind != "generation":
                response["embedding_count"] = len(result.get("embeddings", []))
                response["dimensions"] = sorted({len(v) for v in result.get("embeddings", [])})
                response["parsed_full_response_sha256"] = hashlib.sha256(json_bytes(result)).hexdigest()
            response_path = self.output / "responses" / f"{sequence:03d}.json"
            save_new(response_path, response)
            record.update(outcome="response_received", response_file=response_path.relative_to(self.output).as_posix())
            self.budget.remaining()
            return result
        except Exception as exc:
            record.update(outcome="error", error_type=type(exc).__name__, error=str(exc))
            if self.budget.clock() - self.budget.started >= 1200:
                raise StopRun("overall_timeout") from exc
            raise
        finally:
            record["seconds"] = round(time.monotonic() - start, 6)
            self.settings = previous
            self.journal.write({"event": "post_finished", **record})

    def select(self, question, passages):
        self.generation_inputs[self.case_id] = deepcopy(passages)
        selection = super().select(question, passages)
        self.selections[self.case_id] = selection
        return selection


def preserve_originals(require_local=False):
    snapshot = json.loads((ROOT / "evaluation/proposals/review-v2-preservation.json").read_text())
    optional = {"data/support.sqlite3", "frontend/test-results/actual-local-desktop.png",
                "frontend/test-results/actual-local-mobile.png"}
    checked = {}
    for name, original in snapshot["original_artifact_sha256"].items():
        path = ROOT / name
        if name in optional and not path.exists() and not require_local:
            continue
        allowed = {original, snapshot["original_git_blob_sha256"].get(name, original)}
        actual = sha(path)
        if actual not in allowed:
            raise StopRun("original_artifact_changed:" + name)
        checked[name] = actual
    return checked


def git(*args):
    return subprocess.check_output(["git", "-c", "safe.directory=" + ROOT.as_posix(), *args], cwd=ROOT, timeout=5)


def preflight():
    if git("status", "--porcelain").strip():
        raise StopRun("working_tree_not_clean")
    head = git("rev-parse", "HEAD").decode().strip()
    # Runner-only commits may descend from the authorized baseline; application
    # source, fixture, proposal, and v2 bytes must remain exactly at that baseline.
    git("merge-base", "--is-ancestor", BASELINE, head)
    protected_paths = ("support_agent", "corpus", "evaluation/labels/v2", "evaluation/holdout",
                       "evaluation/proposals", "evaluation/golden.json", "evaluation/baseline-manifest.json")
    if git("diff", "--name-only", BASELINE, head, "--", *protected_paths).strip():
        raise StopRun("approved_source_or_labels_changed")
    for raw in git("ls-tree", "-r", "--name-only", BASELINE, "--", *protected_paths).decode().splitlines():
        if (ROOT / raw).read_bytes().replace(b"\r\n", b"\n") != git("show", BASELINE + ":" + raw):
            raise StopRun("approved_worktree_bytes_changed:" + raw)
    version = ROOT / "evaluation/labels/v2"
    if sha(version / "labels.json") != LABEL_DIGEST or sha(version / "manifest.json") != MANIFEST_DIGEST:
        raise StopRun("immutable_label_digest_mismatch")
    manifest = json.loads((version / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if sha(version / name) != expected:
            raise StopRun("immutable_version_member_changed:" + name)
    labels = json.loads((version / "labels.json").read_text())
    budget = json.loads((version / "execution-budget.json").read_text())
    if budget["models"] != MODELS or budget["max_total_embedding_requests"] != 23 or budget["max_answer_generation_requests"] != 12 or budget["wall_clock_minutes"] != 20:
        raise StopRun("authorized_ceiling_mismatch")
    snapshot = preserve_originals(require_local=True)
    scenarios, ids = {}, set()
    evidence_to_check = list(labels["required_source_locations"]["golden/reset"])
    for case in labels["holdout"]["cases"]:
        if case["id"] in ids:
            raise StopRun("duplicate_case_id")
        ids.add(case["id"])
        scenarios.setdefault(case["scenario"], None)
        evidence_to_check.extend(case["evidence"])
    for evidence in evidence_to_check:
        source = (ROOT / evidence["file"]).read_bytes().decode("utf-8")
        text, _ = normalize(source)
        a, b = evidence["normalized_offsets"]
        x, y = evidence["source_offsets"]
        if text[a:b] != evidence["quote"] or normalize(source[x:y])[0] != evidence["quote"]:
            raise StopRun("approved_evidence_mismatch")
        header = HEADER.match(text)
        ordinal = int(evidence["chunk_id"].rsplit("c", 1)[1])
        passages = list(chunks(text[header.end():]))
        if header.group(1) != evidence["chunk_id"].split(":")[0] or not (passages[ordinal][0]+header.end() <= a < b <= passages[ordinal][1]+header.end()):
            raise StopRun("approved_chunk_location_mismatch")
    for scenario in scenarios:
        count = 0
        for path in (ROOT / "evaluation/holdout/corpora" / scenario).iterdir():
            text, _ = normalize(path.read_bytes().decode("utf-8"))
            count += len(list(chunks(text[HEADER.match(text).end():])))
        if not 1 <= count <= 16:
            raise StopRun("ingestion_batch_plan_mismatch")
        scenarios[scenario] = count
    if len(ids) != 12 or len(scenarios) != 11 or sum(scenarios.values()) != 33:
        raise StopRun("frozen_case_or_ingestion_plan_mismatch")
    return labels, {"approved_application_baseline": BASELINE, "runner_commit": head,
                    "runner_sha256": sha(Path(__file__)), "labels_sha256": LABEL_DIGEST,
                    "label_version": "v2", "scenarios": scenarios, "original_artifacts": snapshot}


def score_case(case, result, settings, model, seconds, index_before, index_after):
    passages = {p["id"]: p for p in result["passages"]}
    groups = {}
    for evidence in case["evidence"]:
        groups.setdefault(evidence["quote"], []).append(evidence)
    hits = []
    for quote, alternatives in groups.items():
        hits.append(any(e["chunk_id"] in passages and any(
            q["quote"] == quote and [q["normalized_start"], q["normalized_end"]] == e["normalized_offsets"]
            for q in passages[e["chunk_id"]]["quotes"]) for e in alternatives))
    citation_checks = []
    with connect(settings.db_path) as db:
        for citation in result["citations"]:
            p = passages.get(citation["chunk_id"])
            d = db.execute("SELECT * FROM documents WHERE id=?", (citation["document_id"],)).fetchone()
            a, b = citation["normalized_start"], citation["normalized_end"]
            x, y = citation["source_start"], citation["source_end"]
            valid = bool(p and d and p["document_id"] == citation["document_id"] and d["version"] == citation["version"]
                         and p["normalized_start"] <= a < b <= p["normalized_end"]
                         and d["normalized"][a:b] == citation["quote"]
                         and normalize(d["source"][x:y])[0] == citation["quote"]
                         and any(q == {k: citation[k] for k in q} for q in p["quotes"]))
            citation_checks.append({"chunk_id": citation["chunk_id"], "quote": citation["quote"], "exact_source_valid": valid})
    quoted = {c["quote"] for c in result["citations"]}
    expected = set(groups)
    abstention_correct = result["abstained"] == case["abstain"]
    rubric_correct = abstention_correct and (case["abstain"] or (quoted == expected and bool(citation_checks) and all(c["exact_source_valid"] for c in citation_checks)))
    reasons = []
    if hits and not all(hits):
        reasons.append("retrieval_miss_or_wrong_chunk")
    if not case["abstain"] and result["abstained"]:
        reasons.append("supported_false_abstention:" + result["diagnostics"]["reason"])
    if case["abstain"] and not result["abstained"]:
        reasons.append("unsupported_false_positive:" + case["classification"])
    if not result["abstained"] and quoted - expected:
        reasons.append("extra_or_irrelevant_exact_source_sentence")
    if not result["abstained"] and expected - quoted:
        reasons.append("incomplete_required_answer")
    if any(not c["exact_source_valid"] for c in citation_checks):
        reasons.append("unsupported_or_hallucinated_final_claim")
    selection = model.selections.get(case["id"])
    raw_checks = []
    if isinstance(selection, dict) and isinstance(selection.get("claims"), list):
        for claim in selection["claims"]:
            valid = isinstance(claim, dict) and isinstance(claim.get("chunk_id"), str) and claim.get("chunk_id") in passages and any(
                q["quote"] == claim.get("quote") for q in passages[claim["chunk_id"]]["quotes"])
            raw_checks.append({"claim": claim, "exact_source_valid": bool(valid)})
    inputs = model.generation_inputs.get(case["id"], [])
    case_calls = [c for c in model.calls if c["case_id"] == case["id"]]
    return {"id": case["id"], "scenario": case["scenario"], "question": case["question"],
            "classification": case["classification"], "expected_abstention": case["abstain"],
            "abstained": result["abstained"], "abstention_correct": abstention_correct,
            "grounded_answer_correct_under_frozen_rubric": rubric_correct,
            "semantic_correctness_note": "Finite agent-curated exact-fact rubric; not independent human semantic validation.",
            "retrieval_recall_at_6": sum(hits)/len(hits) if hits else None,
            "evidence_groups_found": sum(hits), "evidence_groups_required": len(hits),
            "citation_checks": citation_checks, "raw_selection_checks": raw_checks,
            "raw_model_selection": selection, "failure_categories": reasons,
            "reason": result["diagnostics"]["reason"], "seconds": round(seconds, 6),
            "generation_seconds": sum(c["seconds"] for c in case_calls if c["kind"] == "generation"),
            "actual_post_counts": {kind: sum(c["kind"] == kind for c in case_calls) for kind in ("query_embedding", "generation")},
            "index_sha256_before": index_before, "index_sha256_after": index_after,
            "security": {"quarantined_retrieved_ids": [p["id"] for p in result["passages"] if p["quarantined"]],
                         "instructions_reached_generation": any(has_instructions(p["text"]) for p in inputs),
                         "untrusted_instructions_in_final_answer": has_instructions(result["answer"]),
                         "external_actions": 0}, "result": result}


def summary(records):
    def ratio(n, d):
        return {"numerator": n, "denominator": d, "value": n/d if d else None}
    citations = [c for r in records for c in r["citation_checks"]]
    recalls = [r["retrieval_recall_at_6"] for r in records if r["retrieval_recall_at_6"] is not None]
    supported = [r for r in records if not r["expected_abstention"]]
    negative = [r for r in records if r["expected_abstention"]]
    return {"completed_cases": len(records),
            "retrieval_recall_at_6_macro": {"applicable_cases": len(recalls), "value": sum(recalls)/len(recalls) if recalls else None},
            "citation_exact_source_validity": ratio(sum(c["exact_source_valid"] for c in citations), len(citations)),
            "grounded_answer_accuracy_frozen_rubric": ratio(sum(r["grounded_answer_correct_under_frozen_rubric"] for r in records), len(records)),
            "supported_answer_accuracy_frozen_rubric": ratio(sum(r["grounded_answer_correct_under_frozen_rubric"] for r in supported), len(supported)),
            "abstention_accuracy": ratio(sum(r["abstention_correct"] for r in records), len(records)),
            "unsupported_false_positive_answers": ratio(sum(not r["abstained"] for r in negative), len(negative)),
            "supported_false_abstentions": ratio(sum(r["abstained"] for r in supported), len(supported)),
            "correct_abstentions": sum(r["abstained"] for r in negative),
            "incorrect_abstentions": sum(r["abstained"] for r in supported),
            "hallucinated_final_claims_without_exact_source_support": sum(not c["exact_source_valid"] for c in citations),
            "raw_model_claims_without_exact_source_support": sum(not c["exact_source_valid"] for r in records for c in r["raw_selection_checks"]),
            "raw_nonabstaining_selections_on_expected_abstention_cases": [r["id"] for r in negative if isinstance(r["raw_model_selection"], dict) and r["raw_model_selection"].get("abstain") is False],
            "security_quarantine_cases": [{"id": r["id"], "abstained": r["abstained"], "reason": r["reason"], **r["security"]} for r in records if r["classification"] == "security-quarantine"]}


def claim_once(path, payload):
    try:
        save_new(path, payload)
    except FileExistsError as exc:
        raise StopRun("authorization_already_claimed_no_second_batch") from exc


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-authorized-once", action="store_true")
    args = parser.parse_args()
    if not args.execute_authorized_once:
        parser.error("Explicit one-run execution flag required; no HTTP requests made")
    budget = Budget()
    labels, provenance = preflight()
    local = ROOT / ".local" / RUN_ID
    output = ROOT / "reports" / RUN_ID
    claim_once(ROOT / ".local" / (RUN_ID + ".claim.json"), {
        "run_id": RUN_ID, "claimed_at_utc": utc(), "labels_sha256": LABEL_DIGEST,
        "approved_source_baseline": BASELINE, "runner_commit": provenance["runner_commit"],
        "scope": "One explicit owner-authorized execution only; this claim is never deleted or reused."})
    if local.exists() or output.exists():
        raise StopRun("isolated_run_path_already_exists")
    local.mkdir(parents=True)
    output.mkdir(parents=True)
    journal = Journal(output / "requests.jsonl")
    # A hard process deadline is independent of model/socket cancellation. Last
    # checkpoints and the pre-send ledger survive even if the process must exit.
    def hard_deadline():
        try:
            journal.write({"event": "overall_timeout_hard_stop", "no_retry": True})
        finally:
            os._exit(124)
    watchdog = threading.Timer(budget.remaining(), hard_deadline)
    watchdog.daemon = True
    watchdog.start()
    report = {"run_id": RUN_ID, "benchmark_type": "agent-curated synthetic adversarial evaluation; not independently human-validated",
              "actual_local_inference": True, "status": "started", "started_at_utc": utc(),
              "authorization": {"source": "Explicit owner instruction authorizing ONE bounded Phase D run in the current Codex conversation",
                                "baseline": BASELINE, "labels_sha256": LABEL_DIGEST, "max_generation_posts": 12,
                                "max_embedding_posts": 23, "wall_clock_seconds": 1200, "retries": 0,
                                "downloads": 0, "substitutions": 0, "additional_baseline_or_smoke_calls": 0},
              "provenance": provenance, "python": platform.python_version(), "system": platform.system(),
              "ingestions": [], "records": [], "golden_questions_evaluated": 0}
    model = RecordedOllama(Settings(), budget, journal, output)
    def checkpoint():
        report["actual_local_inference"] = any(c.get("outcome") == "response_received" for c in model.calls)
        report["request_counts"] = {**budget.counts, "total_embedding_posts": budget.counts["ingestion_embedding"] + budget.counts["query_embedding"]}
        report["elapsed_seconds"] = round(budget.clock() - budget.started, 6)
        report["summary"] = summary(report["records"])
        temporary = output / "report.json.tmp"
        temporary.write_bytes(json_bytes(report))
        os.replace(temporary, output / "report.json")
    checkpoint()
    try:
        report["identity"] = asyncio.run(identity_gets(budget, journal))
        journal.write({"event": "model_identity_verified", "models": MODELS})
        checkpoint()
        indexes = {}
        for scenario, expected_chunks in sorted(provenance["scenarios"].items()):
            budget.remaining()
            model.phase, model.scenario, model.case_id = "ingestion", scenario, None
            settings = replace(Settings(), db_path=local / "indexes" / (scenario + ".sqlite3"))
            if settings.db_path.exists():
                raise StopRun("index_not_fresh")
            start = time.monotonic()
            receipt = ingest(ROOT / "evaluation/holdout/corpora" / scenario, settings, model)
            if receipt["chunks"] != expected_chunks or receipt["dimension"] != 768 or receipt["unchanged"]:
                raise StopRun("ingested_index_integrity_failure")
            indexes[scenario] = settings
            report["ingestions"].append({"scenario": scenario, **receipt, "seconds": round(time.monotonic()-start, 6),
                                         "index_sha256": sha(settings.db_path), "index_path": settings.db_path.relative_to(ROOT).as_posix()})
            checkpoint()
            print(json.dumps({"ingested": scenario, **receipt}), flush=True)
        seen = set()
        for case in labels["holdout"]["cases"]:
            budget.remaining()
            if case["id"] in seen:
                raise StopRun("repeat_question")
            seen.add(case["id"])
            model.phase, model.scenario, model.case_id = "answering", case["scenario"], case["id"]
            settings = indexes[case["scenario"]]
            before = sha(settings.db_path)
            start = time.monotonic()
            result = answer(case["question"], settings, model)
            elapsed = time.monotonic() - start
            after = sha(settings.db_path)
            record = score_case(case, result, settings, model, elapsed, before, after)
            report["records"].append(record)
            checkpoint()
            print(json.dumps({k: record[k] for k in ("id", "abstained", "reason", "seconds", "grounded_answer_correct_under_frozen_rubric", "failure_categories")}), flush=True)
            if before != after:
                raise StopRun("answering_modified_index")
            if any(c.get("outcome") == "error" for c in model.calls):
                raise StopRun("transport_failure_no_retry")
        budget.remaining()
        report["original_artifacts_after"] = preserve_originals(require_local=True)
        if report["original_artifacts_after"] != provenance["original_artifacts"]:
            raise StopRun("original_artifact_integrity_failure")
        for receipt in report["ingestions"]:
            if sha(ROOT / receipt["index_path"]) != receipt["index_sha256"]:
                raise StopRun("isolated_index_integrity_failure")
        budget.remaining()
        report["status"] = "completed"
    except Exception as exc:
        report.update(status="stopped", stop_reason=str(exc), stop_type=type(exc).__name__)
        journal.write({"event": "run_stopped", "reason": str(exc), "error_type": type(exc).__name__})
    finally:
        report["finished_at_utc"] = utc()
        report["calls"] = model.calls
        report["unused_budget"] = {"generation_posts": 12-budget.counts["generation"], "embedding_posts": 23-budget.counts["ingestion_embedding"]-budget.counts["query_embedding"]}
        report["authorization_consumed"] = True
        report["additional_inference_authorized"] = False
        checkpoint()
        journal.write({"event": "run_finished", "status": report["status"], "counts": budget.counts,
                       "elapsed_seconds": report["elapsed_seconds"], "no_second_batch_authorized": True})
        watchdog.cancel()
    print(json.dumps({"status": report["status"], "request_counts": report["request_counts"], "summary": report["summary"]}, indent=2), flush=True)
    if report["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
