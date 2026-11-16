from .ollama import ModelError
from .retrieval import retrieve
from .text import sentences, terms


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
    verified, seen = [], set()
    for claim in claims:
        if not isinstance(claim, dict) or set(claim) != {"chunk_id", "quote"}:
            return [], "invalid_claim"
        cid, quote = claim["chunk_id"], claim["quote"]
        if not isinstance(cid, str) or not isinstance(quote, str) or cid not in by_id:
            return [], "unverifiable_citation"
        p = by_id[cid]
        if quote not in sentences(p["text"]) or (cid, quote) in seen:
            return [], "unsupported_quote"
        if p["similarity"] < settings.min_similarity or not terms(question).intersection(terms(quote)):
            return [], "insufficient_evidence"
        if p["ambiguous_topic"]:
            return [], "conflicting_sources"
        seen.add((cid, quote))
        start = p["text"].index(quote)
        verified.append({"chunk_id": cid, "document_id": p["document_id"], "title": p["title"],
                         "version": p["version"], "quote": quote,
                         "normalized_start": p["normalized_start"] + start,
                         "normalized_end": p["normalized_start"] + start + len(quote)})
    return verified, "verified_exact_quotes"


def answer(question, settings, model):
    passages = retrieve(question, settings, model)
    result = {"answer": ABSTENTION, "abstained": True, "citations": [], "passages": passages,
              "diagnostics": {"algorithm": "RRF(k=60), equal semantic/FTS5 weights", "generation_called": False,
                              "embedding_model": settings.embed_model, "generation_model": settings.chat_model}}
    relevant = [p for p in passages if p["similarity"] >= settings.min_similarity
                and terms(question).intersection(terms(p["text"])) and sentences(p["text"])]
    if not relevant:
        reason = "insufficient_evidence"
    elif relevant[0]["ambiguous_topic"]:
        reason = "conflicting_sources"
    else:
        result["diagnostics"]["generation_called"] = True
        try:
            selection = model.select(question, [{"chunk_id": p["id"], "text": p["text"]} for p in relevant])
            citations, reason = verify_selection(selection, relevant, question, settings)
            if citations:
                result.update(answer="\n\n".join(f'{c["quote"]} [{c["chunk_id"]}]' for c in citations),
                              abstained=False, citations=citations)
        except ModelError:
            reason = "generation_failed"
    result["diagnostics"]["reason"] = reason
    return result
