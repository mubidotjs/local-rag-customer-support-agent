import hashlib
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
from .config import Settings
from .text import chunks, normalize


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, version TEXT NOT NULL, topic TEXT NOT NULL,
 filename TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL, source TEXT NOT NULL, normalized TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(
 id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id), ordinal INTEGER NOT NULL,
 normalized_start INTEGER NOT NULL, normalized_end INTEGER NOT NULL,
 source_start INTEGER NOT NULL, source_end INTEGER NOT NULL,
 text TEXT NOT NULL, embedding TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(chunk_id UNINDEXED, text, tokenize='unicode61');
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


@contextmanager
def connect(path: Path, *, readonly=True):
    if readonly:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    try:
        with db:
            yield db
    finally:
        db.close()


def ingest(directory: Path, settings: Settings, model):
    """CLI-only ingestion. All validation and embedding happens before atomic storage."""
    root = directory.resolve(strict=True)
    files = sorted(root.iterdir(), key=lambda p: p.name)
    docs, passages = [], []
    for path in files:
        if path.suffix.lower() not in (".md", ".txt"):
            continue
        if path.is_symlink() or not path.is_file() or path.resolve().parent != root:
            raise ValueError("Only regular files directly inside the corpus directory are allowed")
        # Open with a capped read as well as stat to bound races and memory.
        with path.open("rb") as stream:
            raw = stream.read(settings.max_file_bytes + 1)
        if len(raw) > settings.max_file_bytes:
            raise ValueError("Document exceeds byte limit")
        source = raw.decode("utf-8")
        text, offsets = normalize(source)
        match = re.match(r"\A---\n(id: [a-z0-9-]+)\ntitle: ([^\n]{1,120})\nversion: ([^\n]{1,40})\ntopic: ([a-z0-9-]{1,60})\n---\n", text)
        if not match:
            raise ValueError(f"Missing required metadata header: {path.name}")
        doc_id, title, version, topic = match.groups()
        doc_id = doc_id.removeprefix("id: ")
        docs.append((doc_id, title, version, topic, path.name, hashlib.sha256(raw).hexdigest(), source, text))
        for ordinal, (start, end, chunk) in enumerate(chunks(text[match.end():], settings.chunk_chars)):
            start, end = start + match.end(), end + match.end()
            passages.append((f"{doc_id}:c{ordinal:03d}", doc_id, ordinal, start, end,
                             offsets[start][0], offsets[end - 1][1], chunk))
        if len(docs) > settings.max_documents or len(passages) > settings.max_chunks:
            raise ValueError("Corpus exceeds document or chunk limit")
    if not docs or not passages or len({d[0] for d in docs}) != len(docs):
        raise ValueError("Corpus must be nonempty with unique document IDs")
    if settings.db_path.is_file():
        with connect(settings.db_path) as db:
            stored = db.execute("SELECT id,sha256 FROM documents ORDER BY id").fetchall()
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            if stored:
                same_docs = [tuple(r) for r in stored] == sorted((d[0], d[5]) for d in docs)
                if same_docs and meta.get("embed_model") == settings.embed_model and meta.get("chunk_chars") == str(settings.chunk_chars):
                    return {"documents": len(docs), "chunks": len(passages), "unchanged": True}
                raise ValueError("Index is immutable; choose a new RAG_DB for a different corpus")
    vectors = []
    for i in range(0, len(passages), 16):
        vectors.extend(model.embed([p[-1] for p in passages[i:i + 16]]))
    if len(vectors) != len(passages) or len({len(v) for v in vectors}) != 1:
        raise ValueError("Embedding dimensions or count mismatch")
    with connect(settings.db_path, readonly=False) as db:
        db.executescript(SCHEMA)
        existing = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        if existing:
            stored = db.execute("SELECT id,sha256 FROM documents ORDER BY id").fetchall()
            same_docs = [tuple(r) for r in stored] == sorted((d[0], d[5]) for d in docs)
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            if same_docs and meta.get("embed_model") == settings.embed_model and meta.get("chunk_chars") == str(settings.chunk_chars):
                return {"documents": len(docs), "chunks": len(passages), "unchanged": True}
            raise ValueError("Index is immutable; choose a new RAG_DB for a different corpus")
        db.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)", docs)
        db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)",
                       [(*p, json.dumps(v)) for p, v in zip(passages, vectors, strict=True)])
        db.executemany("INSERT INTO chunks_fts VALUES (?,?)", [(p[0], p[-1]) for p in passages])
        db.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("embed_model", settings.embed_model), ("dimension", str(len(vectors[0]))),
            ("chunk_chars", str(settings.chunk_chars)), ("schema_version", "1"),
        ])
    return {"documents": len(docs), "chunks": len(passages), "dimension": len(vectors[0]), "unchanged": False}
