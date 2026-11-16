"""Run at most 12 actual generations; synthetic CI uses pytest instead."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import time
from datetime import datetime, timezone
import httpx
from support_agent.answer import answer
from support_agent.config import SETTINGS
from support_agent.ollama import Ollama
from support_agent.retrieval import retrieve
from support_agent.store import connect


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate", action="store_true", help="Opt in to at most 12 real generations")
    parser.add_argument("--limit", type=int, default=12, choices=range(1, 13))
    parser.add_argument("--output", type=Path, default=Path("reports/local-evaluation.json"))
    args = parser.parse_args()
    model = Ollama(SETTINGS)
    with httpx.Client(trust_env=False, timeout=5) as client:
        models = client.get(SETTINGS.ollama_url + "/api/tags").json()
        version = client.get(SETTINGS.ollama_url + "/api/version").json()
    cases = json.loads(Path("evaluation/golden.json").read_text(encoding="utf-8"))[:args.limit]
    before = hashlib.sha256(SETTINGS.db_path.read_bytes()).hexdigest()
    records = []
    for case in cases:
        start = time.monotonic()
        if args.generate:
            result = answer(case["question"], SETTINGS, model)
        else:
            result = {"passages": retrieve(case["question"], SETTINGS, model)}
        retrieved_docs = {p["document_id"] for p in result["passages"]}
        expected = set(case["documents"])
        recall = len(expected.intersection(retrieved_docs)) / len(expected) if expected else None
        valid = True
        with connect(SETTINGS.db_path) as db:
            for citation in result.get("citations", []):
                document = db.execute("SELECT normalized FROM documents WHERE id=?", (citation["document_id"],)).fetchone()
                valid &= citation["chunk_id"] in {p["id"] for p in result["passages"]}
                valid &= document is not None and document[0][citation["normalized_start"]:citation["normalized_end"]] == citation["quote"]
        abstention_correct = result.get("abstained") == case["abstain"] if args.generate else None
        correctness = (abstention_correct and valid and (case["abstain"] or all(
            q in [c["quote"] for c in result["citations"]] for q in case["quotes"]))) if args.generate else None
        record = {"id": case["id"], "kind": case["kind"], "retrieval_recall_at_6": recall,
                  "citation_validity": valid if args.generate else None, "abstention_correct": abstention_correct,
                  "grounded_answer_correct": correctness, "seconds": round(time.monotonic() - start, 3), "result": result}
        records.append(record)
        print(json.dumps({k: v for k, v in record.items() if k != "result"}), flush=True)
    after = hashlib.sha256(SETTINGS.db_path.read_bytes()).hexdigest()
    def mean(values):
        values = [v for v in values if v is not None]
        return sum(values) / len(values) if values else None
    citations = sum(len(r["result"].get("citations", [])) for r in records)
    report = {"actual_local_inference": True, "synthetic": False, "generated": args.generate,
              "timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(),
              "ollama_version": version, "models": models, "database_sha256_before": before,
              "database_sha256_after": after, "answering_left_index_unchanged": before == after,
              "corpus_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path("corpus").iterdir())},
              "summary": {"cases": len(records), "citations_emitted": citations,
                          "generation_requests": sum(r["result"].get("diagnostics", {}).get("generation_called", False) for r in records),
                          "retrieval_recall_at_6": mean(r["retrieval_recall_at_6"] for r in records),
                          "citation_validity_per_case": mean(r["citation_validity"] for r in records),
                          "grounded_answer_correctness": mean(r["grounded_answer_correct"] for r in records),
                          "abstention_accuracy": mean(r["abstention_correct"] for r in records)}, "records": records}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    if before != after or (args.generate and any(not r["grounded_answer_correct"] for r in records)):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
