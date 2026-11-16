from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
from .config import Settings
from .documents import local_path, read_document
from .ollama import unit


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
    path = Path(path)
    if path.exists() or path.is_symlink():
        local_path(path)
        if path.stat().st_nlink != 1:
            raise ValueError("Hardlinked SQLite indexes are not allowed")
    else:
        ancestor = path.parent
        while not ancestor.exists():
            ancestor = ancestor.parent
        local_path(ancestor)
    if readonly:
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        db.execute("PRAGMA query_only=ON")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("PRAGMA foreign_keys=ON")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA trusted_schema=OFF")
    try:
        with db:
            if readonly:
                db.execute("BEGIN")  # One snapshot across all integrity/retrieval SELECTs.
            yield db
    finally:
        db.close()


def ingest(directory: Path, settings: Settings, model):
    """CLI-only ingestion. All validation and embedding happens before atomic storage."""
    root = local_path(directory)
    files = []
    for count, path in enumerate(root.iterdir(),1):
        if count > 1000:
            raise ValueError("Source directory entry limit exceeded")
        if path.suffix.lower() in (".md", ".txt"):
            files.append(path)
    files.sort(key=lambda p:p.name)
    docs, passages = [], []
    for path in files:
        if path.suffix.lower() not in (".md", ".txt"):
            continue
        document, pieces = read_document(root, path.name, settings)
        docs.append(document)
        passages.extend(pieces)
        if len(docs) > settings.max_documents or len(passages) > settings.max_chunks:
            raise ValueError("Corpus exceeds document or chunk limit")
    if not docs or not passages or len({d[0] for d in docs}) != len(docs) or len({d[4].casefold() for d in docs}) != len(docs):
        raise ValueError("Corpus must be nonempty with unique document IDs")
    if settings.db_path.is_file():
        local_path(settings.db_path)
        if settings.db_path.stat().st_size > settings.max_db_bytes:
            raise ValueError("Index exceeds database size limit")
        with connect(settings.db_path) as db:
            from .integrity import checked_index, checked_schema
            checked_schema(db)
            if db.execute("SELECT count(*) FROM metadata").fetchone()[0]:
                checked_index(db,settings,allow_empty=True)
            stored = db.execute("SELECT id,sha256 FROM documents ORDER BY id").fetchall()
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            if stored:
                same_docs = [tuple(r) for r in stored] == sorted((d[0], d[5]) for d in docs)
                if same_docs and meta.get("embed_model") == settings.embed_model and meta.get("chunk_chars") == str(settings.chunk_chars):
                    from .integrity import checked_index
                    checked_index(db, settings, allow_empty=True)
                    if getattr(model,"binding",None) is not None:
                        from .embeddings import adapter_binding
                        binding = adapter_binding(model,settings)
                        if (meta.get("embed_digest"),meta.get("embed_config")) != (binding.digest,binding.config):
                            raise ValueError("Index identity is immutable; use an explicit full reindex")
                    return {"documents": len(docs), "chunks": len(passages), "unchanged": True}
                raise ValueError("Index is immutable; choose a new RAG_DB for a different corpus")
            if meta:
                raise ValueError("Initialized index is immutable; use explicit lifecycle add after deletion")
    vectors = []
    if getattr(model,"binding",None) is not None:
        from .embeddings import embed_batches
        encoded, _ = embed_batches([p[-1] for p in passages],model,settings)
        vectors = [json.loads(v) for v in encoded]
    else:
        for i in range(0, len(passages), 16):
            result = model.embed([p[-1] for p in passages[i:i + 16]])
            if not isinstance(result, list) or len(result) != len(passages[i:i+16]):
                raise ValueError("Embedding dimensions or count mismatch")
            vectors.extend(unit(v) for v in result)
    if len(vectors) != len(passages) or len({len(v) for v in vectors}) != 1:
        raise ValueError("Embedding dimensions or count mismatch")
    with connect(settings.db_path, readonly=False) as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        from .integrity import checked_schema
        checked_schema(db)
        existing = db.execute("SELECT count(*) FROM documents").fetchone()[0]
        if existing:
            stored = db.execute("SELECT id,sha256 FROM documents ORDER BY id").fetchall()
            same_docs = [tuple(r) for r in stored] == sorted((d[0], d[5]) for d in docs)
            meta = dict(db.execute("SELECT key,value FROM metadata"))
            if same_docs and meta.get("embed_model") == settings.embed_model and meta.get("chunk_chars") == str(settings.chunk_chars):
                from .integrity import checked_index
                checked_index(db,settings,allow_empty=True)
                if getattr(model,"binding",None) is not None:
                    from .embeddings import adapter_binding
                    binding=adapter_binding(model,settings)
                    if (meta.get("embed_digest"),meta.get("embed_config")) != (binding.digest,binding.config):
                        raise ValueError("Concurrent index identity is immutable; preview a full reindex")
                return {"documents": len(docs), "chunks": len(passages), "unchanged": True}
            raise ValueError("Index is immutable; choose a new RAG_DB for a different corpus")
        if db.execute("SELECT count(*) FROM metadata").fetchone()[0]:
            raise ValueError("Initialized index is immutable; use explicit lifecycle add after deletion")
        db.executemany("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)", docs)
        db.executemany("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)",
                       [(*p, json.dumps(v)) for p, v in zip(passages, vectors, strict=True)])
        db.executemany("INSERT INTO chunks_fts VALUES (?,?)", [(p[0], p[-1]) for p in passages])
        db.executemany("INSERT INTO metadata VALUES (?,?)", [
            ("embed_model", settings.embed_model), ("dimension", str(len(vectors[0]))),
            ("chunk_chars", str(settings.chunk_chars)), ("schema_version", "1"),
        ])
        if getattr(model, "binding", None) is not None:
            from .embeddings import adapter_binding
            binding = adapter_binding(model, settings)
            db.executemany("INSERT INTO metadata VALUES (?,?)", [
                ("embed_digest", binding.digest), ("embed_config", binding.config), ("index_revision", "0")])
        from .integrity import checked_index
        checked_index(db, settings)
        if db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0] > settings.max_db_bytes:
            raise ValueError("Index exceeds database size limit")
    return {"documents": len(docs), "chunks": len(passages), "dimension": len(vectors[0]), "unchanged": False}
