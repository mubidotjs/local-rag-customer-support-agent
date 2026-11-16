"""Offline validation replay: no model, model transport, or old index is used."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from unittest.mock import patch
from support_agent.answer import answer, verify_selection
from support_agent.config import Settings
from support_agent.text import HEADER, chunks, normalize, sentence_spans

ROOT = Path(__file__).resolve().parents[2]
HERE = ROOT / "evaluation/relevance"


def preserved(require_local=False):
    snapshot = json.loads((HERE / "preservation.json").read_text())
    for name, expected in snapshot["files"].items():
        path = ROOT / name
        if not path.exists() and not require_local and name in snapshot["optional_in_fresh_ci"]:
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual not in {expected, snapshot["git_representations"].get(name, expected)}:
            raise ValueError("Historical evidence/index changed: " + name)
    return len(snapshot["files"])


def frozen_holdout():
    freeze = json.loads((HERE / "freeze.json").read_text())
    for name, expected in freeze["files"].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != expected:
            raise ValueError("Offline holdout changed after freeze")
    return json.loads((HERE / "holdout.json").read_text())


def checked_recorded_passages(record):
    """Recheck recorded exact passages against preserved source, never old SQLite."""
    passages = deepcopy(record["result"]["passages"])
    for p in passages:
        path = ROOT / "evaluation/holdout/corpora" / record["scenario"] / p["filename"]
        source = path.read_bytes().decode("utf-8")
        normalized, _ = normalize(source)
        header = HEADER.match(normalized)
        if header.groups() != (p["document_id"], p["title"], p["version"], p["topic"]):
            raise ValueError("Recorded source metadata mismatch")
        a, b = p["normalized_start"], p["normalized_end"]
        x, y = p["source_start"], p["source_end"]
        if normalized[a:b] != p["text"] or normalize(source[x:y])[0] != p["text"]:
            raise ValueError("Recorded chunk offsets mismatch")
        body_start = header.end()
        spans = {(start+body_start, end+body_start, quote) for start, end, quote in sentence_spans(normalized[body_start:])}
        for q in p["quotes"]:
            if (q["normalized_start"], q["normalized_end"], q["quote"]) not in spans or not a <= q["normalized_start"] < q["normalized_end"] <= b:
                raise ValueError("Recorded full source sentence mismatch")
            if normalize(source[q["source_start"]:q["source_end"]])[0] != q["quote"]:
                raise ValueError("Recorded quote source offsets mismatch")
    return passages


def replay():
    preserved()
    historical = json.loads((ROOT / "reports/phase-d-v2-once/report.json").read_text())
    labels = json.loads((ROOT / "evaluation/labels/v2/labels.json").read_text())
    by_id = {c["id"]: c for c in labels["holdout"]["cases"]}
    records = []
    for old in historical["records"]:
        case = by_id[old["id"]]
        passages = checked_recorded_passages(old)
        class RecordedSelection:
            calls = 0
            def select(self, question, evidence):
                if old["raw_model_selection"] is None:
                    raise AssertionError("Replay would require an unrecorded model selection")
                self.calls += 1
                return deepcopy(old["raw_model_selection"])
        recorded = RecordedSelection()
        with patch("support_agent.answer.retrieve", return_value=passages):
            result = answer(case["question"], Settings(), recorded)
        citations, reason = result["citations"], result["diagnostics"]["reason"]
        abstained = result["abstained"]
        expected = {e["quote"] for e in case["evidence"]}
        quotes = {c["quote"] for c in citations}
        correct = abstained == case["abstain"] and (case["abstain"] or quotes == expected)
        records.append({"id":case["id"], "classification":case["classification"],
                        "historical_correct":old["grounded_answer_correct_under_frozen_rubric"],
                        "expected_abstention":case["abstain"], "replay_abstained":abstained,
                        "frozen_rubric_correct":correct,"reason":reason,"recorded_selection_used":bool(recorded.calls),
                        "safe_rejection_of_recorded_wrong_output":old["id"] in {"late-amber","adjacent-cobalt"} and abstained,
                        "new_answer_generation_demonstrated":False,
                        "answer":result["answer"],
                        "citations":citations})
    preserved()
    return {"kind":"strictly offline replay of recorded selections; no retrieval or model rerun",
            "real_model_status":"NOT RERUN","model_requests":0,"cases":12,
            "frozen_rubric_correct":sum(r["frozen_rubric_correct"] for r in records),
            "previously_passing_cases_retained":sum(r["historical_correct"] and r["frozen_rubric_correct"] for r in records),
            "safe_rejections":sum(r["safe_rejection_of_recorded_wrong_output"] for r in records),
            "remaining_supported_false_abstentions":[r["id"] for r in records if not r["expected_abstention"] and r["replay_abstained"]],
            "unsupported_answers":[r["id"] for r in records if r["expected_abstention"] and not r["replay_abstained"]],
            "records":records}


def passages_for_documents(documents):
    """Materialize validated-format synthetic passages without embeddings/SQLite."""
    from support_agent.text import has_instructions
    passages = []
    topics = {}
    for d in documents:
        topics.setdefault(d["topic"], []).append(d)
    for d in documents:
        text = f'---\nid: {d["id"]}\ntitle: {d["title"]}\nversion: {d["version"]}\ntopic: {d["topic"]}\n---\n{d["body"]}\n'
        start = HEADER.match(text).end()
        full = list(sentence_spans(text[start:]))
        for ordinal,(a,b,body) in enumerate(chunks(text[start:])):
            a,b = a+start,b+start
            quotes = [{"quote":q,"normalized_start":x+start,"normalized_end":y+start,"source_start":x+start,"source_end":y+start} for x,y,q in full if a <= x+start and y+start <= b]
            passages.append({"id":d["id"]+f':c{ordinal:03d}',"document_id":d["id"],"title":d["title"],"version":d["version"],
                             "text":body,"normalized_start":a,"normalized_end":b,"source_start":a,"source_end":b,
                             "quotes":quotes,"similarity":1.0,"quarantined":has_instructions(text),
                             "ambiguous_topic":len(topics[d["topic"]])>1})
    return passages


def holdout():
    data=frozen_holdout()
    documents={d["id"]:d for d in data["documents"]}
    records=[]
    for case in data["cases"]:
        passages=passages_for_documents([documents[d] for d in case["documents"]])
        citations,reason=verify_selection({"abstain":False,"claims":case["claims"]},passages,case["question"],Settings())
        outcome="accept" if citations else "abstain"
        records.append({"id":case["id"],"expected":case["expected"],"actual":outcome,"correct":outcome==case["expected"],"reason":reason,"citations":citations})
    return {"kind":"separately authored frozen agent-curated offline holdout; not human-validated or live-model acceptance", "real_model_status":"NOT RERUN","model_requests":0,
            "cases":len(records),"correct":sum(r["correct"] for r in records),"records":records}


if __name__ == "__main__":
    print(json.dumps({"replay":replay(),"holdout":holdout()},indent=2))
