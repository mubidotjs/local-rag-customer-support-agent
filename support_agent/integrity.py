"""Bounded, read-only validation before model access.

Detects internal corruption, not a coherent rewrite of all source data and hashes.
The local owner remains the corpus trust authority.
"""
import hashlib
import json
import re
from pathlib import Path
from .ollama import ModelError, unit
from .store import SCHEMA
from .text import HEADER, chunks, has_instructions, normalize, sentence_spans


def checked_index(db, settings):
    def reject():
        raise ModelError("Stored index integrity check failed")

    def sql_form(text):
        return re.sub(r"\s+", " ", text.replace(" IF NOT EXISTS", "")).strip().lower()

    # Check the four application objects before querying them. In particular, an
    # attacker-supplied view must not turn a bounded table scan into arbitrary SQL.
    for statement in SCHEMA.split(";"):
        if not statement.strip():
            continue
        name = re.search(r"TABLE IF NOT EXISTS (\w+)", statement).group(1)
        obj = db.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if obj is None or obj[0] != "table" or sql_form(obj[1]) != sql_form(statement):
            reject()

    for table, limit in (("documents", settings.max_documents), ("chunks", settings.max_chunks), ("chunks_fts", settings.max_chunks), ("metadata", 8)):
        if not 1 <= db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] <= limit:
            reject()
    if db.execute("""SELECT 1 FROM documents WHERE
        length(CAST(source AS BLOB)) > ? OR length(CAST(normalized AS BLOB)) > ?
        OR length(id)>60 OR length(title)>120 OR length(version)>40
        OR length(topic)>60 OR length(filename)>255 OR length(sha256)!=64 LIMIT 1""",
        (settings.max_file_bytes, settings.max_file_bytes)).fetchone():
        reject()
    if db.execute("SELECT 1 FROM chunks WHERE length(text)>? OR length(embedding)>120000 LIMIT 1", (settings.chunk_chars,)).fetchone():
        reject()
    if db.execute("SELECT 1 FROM metadata WHERE length(key)>40 OR length(value)>120 LIMIT 1").fetchone():
        reject()
    meta = dict(db.execute("SELECT key,value FROM metadata"))
    if set(meta) != {"embed_model", "dimension", "chunk_chars", "schema_version"}:
        reject()
    if meta["embed_model"] != settings.embed_model or meta["schema_version"] != "1" or meta["chunk_chars"] != str(settings.chunk_chars):
        reject()
    try:
        dimension = int(meta["dimension"])
    except (ValueError, TypeError):
        reject()
    if not 1 <= dimension <= 4096 or str(dimension) != meta["dimension"]:
        reject()
    documents, expected = {}, {}
    for source in db.execute("SELECT * FROM documents ORDER BY id"):
        d = dict(source)
        if not all(isinstance(v, str) for v in d.values()):
            reject()
        text, offsets = normalize(d["source"])
        header = HEADER.match(text)
        if not header or header.groups() != (d["id"], d["title"], d["version"], d["topic"]):
            reject()
        if text != d["normalized"] or hashlib.sha256(d["source"].encode("utf-8")).hexdigest() != d["sha256"]:
            reject()
        if Path(d["filename"]).name != d["filename"] or "/" in d["filename"] or "\\" in d["filename"]:
            reject()
        body_start = header.end()
        full_sentences = list(sentence_spans(text[body_start:]))
        d["fact_signature"] = (d["version"], tuple(q for _, _, q in full_sentences))
        d["quarantined"] = has_instructions(text)
        documents[d["id"]] = d
        for ordinal, (start, end, body) in enumerate(chunks(text[body_start:], settings.chunk_chars)):
            start, end = start + body_start, end + body_start
            cid = f'{d["id"]}:c{ordinal:03d}'
            quotes = []
            for a, b, quote in full_sentences:
                a, b = a + body_start, b + body_start
                if start <= a and b <= end:
                    quotes.append({"quote": quote, "normalized_start": a, "normalized_end": b,
                                   "source_start": offsets[a][0], "source_end": offsets[b-1][1]})
            expected[cid] = (d["id"], ordinal, start, end, offsets[start][0], offsets[end-1][1], body, quotes)
            if len(expected) > settings.max_chunks:
                reject()
    rows = []
    for row in db.execute("SELECT * FROM chunks ORDER BY id"):
        p = dict(row)
        if not all(isinstance(p[k], str) for k in ("id", "document_id", "text", "embedding")):
            reject()
        actual = tuple(p[k] for k in ("document_id", "ordinal", "normalized_start", "normalized_end", "source_start", "source_end", "text"))
        wanted = expected.pop(p["id"], None)
        if wanted is None or actual != wanted[:7] or any(type(p[k]) is not int for k in ("ordinal", "normalized_start", "normalized_end", "source_start", "source_end")):
            reject()
        try:
            p["vector"] = unit(json.loads(p.pop("embedding")))
        except (ValueError, TypeError, OverflowError) as exc:
            raise ModelError("Invalid stored embedding") from exc
        if len(p["vector"]) != dimension:
            reject()
        d = documents[p["document_id"]]
        p.update({k: d[k] for k in ("title", "version", "topic", "filename", "quarantined")})
        p["quotes"] = wanted[7]
        rows.append(p)
    if expected:
        reject()
    fts = [(r[0], r[1]) for r in db.execute("SELECT chunk_id,text FROM chunks_fts ORDER BY chunk_id")]
    if fts != [(p["id"], p["text"]) for p in rows]:
        reject()
    topics = {}
    for d in documents.values():
        topics.setdefault(d["topic"], []).append(d["fact_signature"])
    for p in rows:
        signatures = topics[p["topic"]]
        p["ambiguous_topic"] = len(signatures) > 1 and (len(set(signatures)) != 1 or not signatures[0][1])
    return rows, dimension
