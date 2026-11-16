"""Deterministic contract exercise, NOT an embedding/model-quality evaluation."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from support_agent.answer import answer
from support_agent.config import Settings
from support_agent.store import ingest

ROOT = Path(__file__).resolve().parents[2]
HOLDOUT = ROOT / "evaluation" / "holdout"


def frozen_cases():
    freeze = json.loads((HOLDOUT / "freeze.json").read_text(encoding="utf-8"))
    found = {p.relative_to(HOLDOUT).as_posix() for p in (HOLDOUT / "corpora").rglob("*") if p.is_file()} | {"cases.json"}
    if found != set(freeze["files"]):
        raise ValueError("Frozen holdout file list changed")
    for name, digest in freeze["files"].items():
        if hashlib.sha256((HOLDOUT / name).read_bytes()).hexdigest() != digest:
            raise ValueError("Frozen holdout content changed")
    return json.loads((HOLDOUT / "cases.json").read_text(encoding="utf-8"))


def baseline_preserved():
    manifest = json.loads((ROOT / "evaluation/baseline-manifest.json").read_text())
    for group in ("required", "local_artifacts"):
        for name, digest in manifest[group].items():
            path = ROOT / name
            if group == "local_artifacts" and not path.exists():
                continue  # Ignored local files are not present on a fresh CI checkout.
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError(f"Baseline artifact changed: {name}")
    return True


class ScriptedModel:
    """Constant vectors + fixed selections exercise guards, never simulate AI skill."""
    def __init__(self, case=None):
        self.case = case
        self.embedding_calls = 0
        self.generation_calls = 0

    def embed(self, texts, *, query=False):
        self.embedding_calls += 1
        return [[1.0, 0.0, 0.0] for _ in texts]

    def select(self, question, passages):
        self.generation_calls += 1
        # An oracle chooses independently frozen expected quotes. This is deliberately
        # stronger than a real generator and says nothing about actual semantic recall.
        wanted = list(dict.fromkeys(e["quote"] for e in self.case["evidence"]))
        if not wanted:
            wanted = [q for p in passages for q in p["quotes"]][:1]
        claims = []
        for quote in wanted:
            passage = next((p for p in passages if quote in p["quotes"]), None)
            if passage:
                claims.append({"chunk_id": passage["chunk_id"], "quote": quote})
        return {"abstain": not bool(claims), "claims": claims[:3]}


def run_offline():
    package = frozen_cases()
    baseline_preserved()
    records = []
    with TemporaryDirectory(prefix="rag-adversarial-offline-") as temp:
        for case in package["cases"]:
            settings = replace(Settings(), db_path=Path(temp) / case["id"] / "synthetic.sqlite3")
            model = ScriptedModel(case)
            ingest(HOLDOUT / "corpora" / case["scenario"], settings, model)
            before = hashlib.sha256(settings.db_path.read_bytes()).hexdigest()
            result = answer(case["question"], settings, model)
            after = hashlib.sha256(settings.db_path.read_bytes()).hexdigest()
            expected_quotes = {e["quote"] for e in case["evidence"]}
            actual_quotes = {c["quote"] for c in result["citations"]}
            matched = result["abstained"] == case["abstain"] and (case["abstain"] or expected_quotes <= actual_quotes)
            records.append({"id": case["id"], "classification": case["classification"],
                            "label_expected_abstention": case["abstain"], "scripted_abstained": result["abstained"],
                            "scripted_outcome_matches_label": matched, "reason": result["diagnostics"]["reason"],
                            "synthetic_generation_calls": model.generation_calls,
                            "database_unchanged": before == after,
                            "citations": result["citations"], "retrieved_ids": [p["id"] for p in result["passages"]]})
    baseline_preserved()
    return {"synthetic": True, "actual_local_inference": False, "live_metrics": None,
            "meaning": "Scripted/oracle guard checks only. No real semantic retrieval or model correctness score.",
            "label_status": package["label_status"],
            "labels_sha256": hashlib.sha256((HOLDOUT / "cases.json").read_bytes()).hexdigest(),
            "real_embedding_requests": 0, "real_generation_requests": 0,
            "baseline_preserved": True, "cases": len(records),
            "scripted_outcomes_matching_labels": sum(r["scripted_outcome_matches_label"] for r in records),
            "known_false_abstentions": [r["id"] for r in records if not r["label_expected_abstention"] and r["scripted_abstained"]],
            "records": records}


if __name__ == "__main__":
    print(json.dumps(run_offline(), indent=2))
