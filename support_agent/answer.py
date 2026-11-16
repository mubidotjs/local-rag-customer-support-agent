from .ollama import ModelError
from .retrieval import retrieve
from .relevance import Grounding
from .text import answer_shape_matches, clauses_supported, has_instructions, terms


ABSTENTION = "I cannot answer that from the available documentation."


def verify_selection(selection, passages, question, settings):
    if not isinstance(selection, dict) or set(selection) != {"abstain", "claims"}:
        return [], "invalid_model_output"
    if type(selection["abstain"]) is not bool or not isinstance(selection["claims"], list):
        return [], "invalid_model_output"
    if selection["abstain"]:
        return [], "model_abstained"
    claims = selection["claims"]
    if not 1 <= len(claims) <= 3:
        return [], "invalid_claim_count"
    by_id = {p["id"]: p for p in passages}
    if len(by_id) != len(passages):
        return [], "invalid_evidence"
    grounding = Grounding(question, passages, settings)
    all_proofs = []
    verified, seen = [], set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"chunk_id", "quote"}:
            return [], "invalid_claim"
        cid, quote = claim["chunk_id"], claim["quote"]
        if not isinstance(cid, str) or not isinstance(quote, str) or cid not in by_id:
            return [], "unverifiable_citation"
        p = by_id[cid]
        exact = next((q for q in p["quotes"] if q["quote"] == quote), None)
        if exact is None or (cid, quote) in seen:
            return [], "unsupported_quote"
        if p["quarantined"] or has_instructions(quote):
            return [], "untrusted_source"
        if p["similarity"] < settings.min_similarity:
            return [], "insufficient_evidence"
        if p["ambiguous_topic"]:
            return [], "conflicting_sources"
        proofs, reason = grounding.prove(cid, quote)
        if not proofs:
            # Unknown/nonanswer sentences still face the old necessary numeric
            # guard. Recognized clauses use their own typed facet (a configuration
            # action in a compound duration/action question need not be numeric).
            if not answer_shape_matches(question, quote):
                return [], "answer_shape_mismatch"
            return [], reason
        seen.add((cid, quote))
        verified.append({"chunk_id": cid, "document_id": p["document_id"], "title": p["title"],
                         "version": p["version"], **exact, "relevance": proofs})
        all_proofs.extend(proofs)
    if not grounding.coverage(all_proofs):
        return [], "incomplete_evidence"
    return verified, "verified_exact_quotes"


def answer(question, settings, model):
    passages = retrieve(question, settings, model)
    grounding = Grounding(question, passages, settings)
    result = {"answer": ABSTENTION, "abstained": True, "citations": [], "passages": passages,
              "diagnostics": {"algorithm": "RRF(k=60), equal semantic/FTS5 weights", "generation_called": False,
                              "embedding_model": settings.embed_model, "generation_model": settings.chat_model}}
    relevant = [p for p in passages if p["similarity"] >= settings.min_similarity
                and p["quotes"] and (terms(question).intersection(terms(p["text"]))
                or any(grounding.prove(p["id"], q["quote"])[0] for q in p["quotes"]))]
    if has_instructions(question):
        reason = "untrusted_question"
    elif not relevant:
        reason = "insufficient_evidence"
    elif relevant[0]["quarantined"]:
        reason = "untrusted_source"
    elif relevant[0]["ambiguous_topic"]:
        reason = "conflicting_sources"
    elif not clauses_supported(question, [q["quote"] for p in relevant if not p["quarantined"] and not p["ambiguous_topic"] for q in p["quotes"]]):
        reason = "incomplete_evidence"
    else:
        result["diagnostics"]["generation_called"] = True
        try:
            safe = [p for p in relevant if not p["quarantined"] and not p["ambiguous_topic"]]
            selection = model.select(question, [{"chunk_id": p["id"], "text": p["text"], "quotes": [q["quote"] for q in p["quotes"]]} for p in safe])
            citations, reason = verify_selection(selection, safe, question, settings)
            if citations:
                result.update(answer="\n\n".join(f'{c["quote"]} [{c["chunk_id"]}]' for c in citations),
                              abstained=False, citations=citations)
        except ModelError:
            reason = "generation_failed"
    result["diagnostics"]["reason"] = reason
    return result
