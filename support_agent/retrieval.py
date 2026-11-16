import json
from .ollama import ModelError, unit
from .store import connect
from .text import terms


def retrieve(question, settings, model):
    with connect(settings.db_path) as db:
        meta = dict(db.execute("SELECT key,value FROM metadata"))
        if meta.get("embed_model") != settings.embed_model:
            raise ModelError("Index embedding model mismatch")
        rows = [dict(r) for r in db.execute("""
            SELECT c.*, d.title,d.version,d.topic,d.filename FROM chunks c
            JOIN documents d ON d.id=c.document_id ORDER BY c.id LIMIT ?
        """, (settings.max_chunks + 1,))]
        if len(rows) > settings.max_chunks:
            raise ModelError("Stored chunk limit exceeded")
        query = model.embed([question], query=True)[0]
        if len(query) != int(meta["dimension"]):
            raise ModelError("Query embedding dimension mismatch")
        for row in rows:
            vector = unit(json.loads(row.pop("embedding")))
            if len(vector) != len(query):
                raise ModelError("Stored embedding dimension mismatch")
            row["similarity"] = sum(a * b for a, b in zip(query, vector, strict=True))
        semantic = sorted(rows, key=lambda r: (-r["similarity"], r["id"]))[:20]
        words = sorted(terms(question))[:32]
        expression = " OR ".join('"' + word + '"' for word in words)
        keyword = db.execute("""SELECT chunk_id, bm25(chunks_fts) AS score FROM chunks_fts
            WHERE chunks_fts MATCH ? ORDER BY score,chunk_id LIMIT 20""", (expression,)).fetchall() if expression else []
        sr = {r["id"]: i + 1 for i, r in enumerate(semantic)}
        kr = {r["chunk_id"]: i + 1 for i, r in enumerate(keyword)}
        ranked = []
        for row in rows:
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
        # Multiple active sources for one topic are conservatively ambiguous.
        topics = {}
        for row in db.execute("SELECT topic,id FROM documents"):
            topics.setdefault(row["topic"], []).append(row["id"])
        for row in selected:
            row["ambiguous_topic"] = len(topics[row["topic"]]) > 1
    return selected
