from .integrity import checked_index
from .ollama import ModelError, unit
from .store import connect
from .text import terms


def retrieve(question, settings, model):
    if not isinstance(question, str) or not 3 <= len(question.strip()) <= 600:
        raise ValueError("Question must contain 3 to 600 characters")
    if settings.db_path.stat().st_size > settings.max_db_bytes:
        raise ModelError("Stored database exceeds size limit")
    with connect(settings.db_path) as db:
        rows, dimension = checked_index(db, settings)
        vectors = model.embed([question], query=True)
        if not isinstance(vectors, list) or len(vectors) != 1:
            raise ModelError("Query embedding count mismatch")
        query = unit(vectors[0])
        if len(query) != dimension:
            raise ModelError("Query embedding dimension mismatch")
        canonical, by_id = {}, {}
        for row in rows:
            row["similarity"] = sum(a * b for a, b in zip(query, row.pop("vector"), strict=True))
            row["duplicate_ids"] = []
            if row["text"] not in canonical:
                canonical[row["text"]] = row
            else:
                kept = canonical[row["text"]]
                kept["duplicate_ids"].append(row["id"])
                kept["ambiguous_topic"] |= row["ambiguous_topic"]
                kept["quarantined"] |= row["quarantined"]
            by_id[row["id"]] = canonical[row["text"]]["id"]
        unique = list(canonical.values())
        semantic = sorted(unique, key=lambda r: (-r["similarity"], r["id"]))[:20]
        words = sorted(terms(question))[:32]
        expression = " OR ".join('"' + word + '"' for word in words)
        # Collapse the bounded full match list before applying the candidate cap.
        matches = db.execute("""SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts
            WHERE chunks_fts MATCH ? ORDER BY score,chunk_id LIMIT ?""",
            (expression, settings.max_chunks + 1)).fetchall() if expression else []
        keyword = []
        for r in matches:
            cid = by_id[r["chunk_id"]]
            if cid not in keyword:
                keyword.append(cid)
            if len(keyword) == 20:
                break
        sr = {r["id"]: i + 1 for i, r in enumerate(semantic)}
        kr = {cid: i + 1 for i, cid in enumerate(keyword)}
        ranked = []
        for row in unique:
            sid = row["id"]
            if sid not in sr and sid not in kr:
                continue
            row["semantic_rank"], row["keyword_rank"] = sr.get(sid), kr.get(sid)
            row["rrf_score"] = (1 / (60 + sr[sid]) if sid in sr else 0) + (1 / (60 + kr[sid]) if sid in kr else 0)
            ranked.append(row)
        ranked.sort(key=lambda r: (-r["rrf_score"], r["id"]))
        selected, used = [], 0
        for row in ranked:
            if used + len(row["text"]) > settings.max_context_chars:
                continue
            selected.append(row)
            used += len(row["text"])
            if len(selected) == settings.top_k:
                break
    return selected
