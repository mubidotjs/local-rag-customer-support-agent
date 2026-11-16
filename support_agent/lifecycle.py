"""CLI-only atomic document changes. Preview and apply never infer deletions."""
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
from .documents import local_path, read_document
from .embeddings import Binding, embed_batches
from .integrity import checked_index, checked_schema
from .store import connect
from .text import HEADER, chunks, normalize


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def database_path(settings):
    path = local_path(settings.db_path)
    if not path.is_file() or path.stat().st_nlink != 1 or path.stat().st_size > settings.max_db_bytes:
        raise ValueError("Select a regular bounded local SQLite index")
    for suffix in ("-journal","-wal","-shm"):
        sidecar=Path(str(path)+suffix)
        if sidecar.exists() or sidecar.is_symlink():
            local_path(sidecar)
            if sidecar.stat().st_nlink != 1 or sidecar.stat().st_size > 2*settings.max_db_bytes+4096:
                raise ValueError("Unsafe or oversized SQLite journal/sidecar")
    return path


def snapshot(db, settings):
    # Read old chunk/model settings only to validate an existing generation. A
    # changed model or chunker can be applied solely by explicit full reindex.
    checked_schema(db)
    if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
        raise ValueError("Lifecycle requires SQLite DELETE journal mode; WAL synchronization is not supported")
    if db.execute("SELECT 1 FROM metadata WHERE length(key)>40 OR length(value)>120 LIMIT 1").fetchone():
        raise ValueError("Stored metadata exceeds limits")
    meta_rows = db.execute("SELECT key,value FROM metadata LIMIT 9").fetchall()
    meta = dict(meta_rows)
    try:
        size = int(meta["chunk_chars"])
        if not 1 <= size <= 800 or len(meta["embed_model"]) > 120:
            raise ValueError()
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("Invalid stored index configuration") from exc
    old_settings = replace(settings, chunk_chars=size, embed_model=meta["embed_model"])
    checked_index(db, old_settings, allow_empty=True, allow_old_config=True)
    state = {"metadata": sorted(meta.items()),
             "documents": [tuple(r) for r in db.execute("SELECT * FROM documents ORDER BY id")],
             "chunks": [tuple(r) for r in db.execute("SELECT * FROM chunks ORDER BY id")],
             "fts": [tuple(r) for r in db.execute("SELECT chunk_id,text FROM chunks_fts ORDER BY chunk_id")]}
    return state, meta


def pieces_for_stored(document, settings):
    text, offsets = normalize(document[6])
    start = HEADER.match(text).end()
    pieces = []
    for ordinal, (a,b,body) in enumerate(chunks(text[start:], settings.chunk_chars)):
        a,b = a+start,b+start
        pieces.append((f"{document[0]}:c{ordinal:03d}",document[0],ordinal,a,b,offsets[a][0],offsets[b-1][1],body))
        if len(pieces)>settings.max_chunks:
            raise ValueError("Document exceeds chunk limit")
    return pieces


def proposed(state, meta, settings, operation, document_id, directory, filename, binding):
    if operation not in {"add", "update", "delete", "reindex"}:
        raise ValueError("Unknown document operation")
    if not 1 <= settings.chunk_chars <= 800:
        raise ValueError("Invalid chunk configuration")
    old_docs = {d[0]:d for d in state["documents"]}
    docs = dict(old_docs)
    if operation in {"add", "update"}:
        if directory is None or filename is None:
            raise ValueError("Add/update requires an explicitly selected directory and filename")
        document, pieces = read_document(directory, filename, settings)
        if operation == "add":
            if document_id is not None or document[0] in docs:
                raise ValueError("Add requires a new unique document ID")
        elif document_id not in docs or document[0] != document_id:
            raise ValueError("Update must preserve the selected existing document ID")
        document_id = document[0]
        docs[document_id] = document
    elif operation == "delete":
        if document_id not in docs or directory is not None or filename is not None:
            raise ValueError("Delete requires one selected existing document ID")
        del docs[document_id]
    elif any(x is not None for x in (document_id, directory, filename)):
        raise ValueError("Reindex uses stored sources, not a directory synchronization")
    if len(docs) > settings.max_documents or len({d[4].casefold() for d in docs.values()}) != len(docs):
        raise ValueError("Document limit or duplicate filename")
    if operation != "delete":
        if not isinstance(binding, Binding):
            raise ValueError("An explicit embedding identity is required")
        binding.validate(settings)
        compatible = (meta.get("embed_model"),meta.get("embed_digest"),meta.get("embed_config"),meta.get("chunk_chars")) == (binding.model,binding.digest,binding.config,str(settings.chunk_chars))
        if not compatible and operation != "reindex":
            raise ValueError("Index identity/configuration is unbound or incompatible; preview a full reindex first")
    else:
        compatible = True  # No embeddings are accessed or rebound by deletion.
    wanted = []
    for doc in sorted(docs.values()):
        if operation == "reindex" or doc[0] == document_id:
            wanted.extend(pieces_for_stored(doc, settings))
        else:
            wanted.extend(p[:-1] for p in state["chunks"] if p[1] == doc[0])
        if len(wanted) > settings.max_chunks:
            raise ValueError("Index exceeds chunk limit")
    # Exact text equality within the same document, never numeric/semantic
    # similarity. Offsets/ordinals are independently recomputed when shifted.
    old_by_id = {p[0]:p for p in state["chunks"]}
    reserved = {p[0] for p in wanted if compatible and p[0] in old_by_id and old_by_id[p[0]][1]==p[1] and old_by_id[p[0]][7]==p[7]}
    used=set(reserved)
    rows = []
    for p in wanted:
        old=old_by_id.get(p[0])
        # Duplicate text may have different historical floating-point vectors.
        # Reserve exact IDs first, then map remaining old chunks one-to-one by
        # text. An extra newly duplicated chunk still needs a new vector.
        vector=old[8] if p[0] in reserved else None
        if compatible and vector is None:
            old=next((r for r in state["chunks"] if r[0] not in used and r[1]==p[1] and r[7]==p[7]),None)
            if old:
                used.add(old[0])
                vector=old[8]
        rows.append((*p,vector))
    unchanged = sum(old_by_id.get(p[0]) == p for p in rows)
    removed = sorted(set(old_by_id)-{p[0] for p in rows})
    summary = {"documents_before":len(old_docs),"documents_after":len(docs),"chunks_before":len(old_by_id),"chunks_after":len(rows),
               "vectors_reused":sum(p[8] is not None for p in rows),"vectors_required":sum(p[8] is None for p in rows),
               "unchanged_chunk_rows":unchanged,"removed_chunk_ids":removed,
               "selected_document":document_id,"previous_version":old_docs[document_id][2] if document_id in old_docs else None,
               "new_version":docs[document_id][2] if document_id in docs else None}
    summary["changed_chunks"] = [{"id":p[0],"normalized_offsets":[p[3],p[4]],"source_offsets":[p[5],p[6]],
                                 "text_sha256":hashlib.sha256(p[7].encode()).hexdigest()} for p in rows if old_by_id.get(p[0]) != p]
    changed = sorted(docs.values()) != state["documents"] or rows != state["chunks"] or (operation == "reindex" and not compatible)
    summary["no_op"] = not changed
    return sorted(docs.values()), rows, summary


def preview(settings, operation, *, document_id=None, directory=None, filename=None, binding=None):
    path = database_path(settings)
    if directory is not None:
        directory = str(local_path(directory))
    with connect(path) as db:
        state, meta = snapshot(db, settings)
    _, _, summary = proposed(state, meta, settings, operation, document_id, directory, filename, binding)
    plan = {"format":1,"database":str(path),"database_identity":[path.stat().st_dev,path.stat().st_ino],
            "base":digest(state),"operation":operation,"document_id":document_id,"directory":directory,"filename":filename,
            "binding":asdict(binding) if binding is not None else None,
            "limits":{k:getattr(settings,k) for k in ("chunk_chars","max_documents","max_chunks","max_file_bytes","max_db_bytes")},
            "source_sha256":None,"summary":summary}
    if directory is not None:
        plan["source_sha256"] = read_document(directory,filename,settings)[0][5]
        # Re-evaluate once against the pinned bytes to catch preview-time changes.
        _,_,second = proposed(state,meta,settings,operation,document_id,directory,filename,binding)
        if second != summary or read_document(directory,filename,settings)[0][5] != plan["source_sha256"]:
            raise ValueError("Source changed during preview")
    return {"plan":plan,"confirmation":digest(plan)}


def apply(settings, envelope, adapter=None, *, confirmation=None):
    # Always reconstruct from trusted code and current data. The confirmation is
    # a review token, not authentication against a malicious local owner.
    if not isinstance(envelope,dict) or set(envelope) != {"plan","confirmation"} or not isinstance(envelope["plan"],dict):
        raise ValueError("Invalid preview envelope")
    plan = envelope["plan"]
    if envelope["confirmation"] != digest(plan) or confirmation != envelope["confirmation"]:
        raise ValueError("Explicit confirmation of this exact preview is required")
    try:
        binding = Binding(**plan["binding"]) if plan["binding"] else None
        options = dict(document_id=plan["document_id"],directory=plan["directory"],filename=plan["filename"],binding=binding)
        current = preview(settings,plan["operation"],**options)
    except (KeyError,TypeError) as exc:
        raise ValueError("Invalid preview fields") from exc
    if current != envelope:
        raise ValueError("Stale preview: index, source, settings or operation changed")
    path = database_path(settings)
    with connect(path) as db:
        state, meta = snapshot(db, settings)
    docs, rows, summary = proposed(state,meta,settings,plan["operation"],**options)
    if digest(state) != plan["base"] or summary != plan["summary"] or (plan["source_sha256"] is not None and next(d[5] for d in docs if d[0] == summary["selected_document"]) != plan["source_sha256"]):
        raise ValueError("Stale preview before embedding")
    if summary["no_op"]:
        return {**summary,"applied":False,"embedding_batches":0}
    needed = [p[7] for p in rows if p[8] is None]
    if needed:
        if adapter is None or getattr(adapter,"binding",None) != binding:
            raise ValueError("The confirmed embedding adapter is required; no automatic model requests")
        dimension = int(meta["dimension"]) if state["chunks"] and (plan["operation"] != "reindex" or meta.get("embed_digest") == binding.digest and meta.get("embed_config") == binding.config) else None
        vectors, dimension = embed_batches(needed,adapter,settings,dimension)
    else:
        vectors, dimension = [], int(meta["dimension"])
    generated = iter(vectors)
    rows = [(*p[:8],next(generated) if p[8] is None else p[8]) for p in rows]
    with connect(path,readonly=False) as db:
        db.execute("BEGIN IMMEDIATE")  # Reserved writer lock; readers see old state until commit.
        locked_state, _ = snapshot(db,settings)
        # Recheck source bytes and identity after embedding, under the writer lock.
        # preview uses a second read connection; the reserved lock permits it.
        if digest(locked_state) != plan["base"] or preview(settings,plan["operation"],**options) != envelope:
            raise ValueError("Stale preview during application")
        old_rows = {p[0]:p for p in state["chunks"]}
        new_rows = {p[0]:p for p in rows}
        for cid, old in old_rows.items():
            if new_rows.get(cid) != old:
                db.execute("DELETE FROM chunks_fts WHERE chunk_id=?",(cid,))
                db.execute("DELETE FROM chunks WHERE id=?",(cid,))
        for old in state["documents"]:
            if old[0] not in {d[0] for d in docs}:
                db.execute("DELETE FROM documents WHERE id=?",(old[0],))
        for doc in docs:
            db.execute("INSERT INTO documents VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,version=excluded.version,topic=excluded.topic,filename=excluded.filename,sha256=excluded.sha256,source=excluded.source,normalized=excluded.normalized",doc)
        for cid, row in new_rows.items():
            if old_rows.get(cid) != row:
                db.execute("INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?)",row)
                db.execute("INSERT INTO chunks_fts VALUES (?,?)",(cid,row[7]))
        changes = {"index_revision":str(int(meta.get("index_revision","0"))+1)}
        if plan["operation"] != "delete":
            changes.update(embed_model=binding.model,embed_digest=binding.digest,embed_config=binding.config,
                           chunk_chars=str(settings.chunk_chars),dimension=str(dimension))
        db.executemany("INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",changes.items())
        final_settings = settings if plan["operation"] != "delete" else replace(settings,embed_model=meta["embed_model"],chunk_chars=int(meta["chunk_chars"]))
        checked_index(db,final_settings,allow_empty=True,allow_old_config=plan["operation"]=="delete")
        # Also validate the actual FTS5 inverted index, not only its content rows.
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('integrity-check')")
        if db.execute("PRAGMA page_count").fetchone()[0] * db.execute("PRAGMA page_size").fetchone()[0] > settings.max_db_bytes:
            raise ValueError("Index exceeds database size limit")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Foreign key integrity failure")
        current_path=database_path(settings)
        if [current_path.stat().st_dev,current_path.stat().st_ino] != plan["database_identity"]:
            raise ValueError("Index file changed before commit")
        # context manager COMMIT errors also roll back and propagate.
    return {**summary,"applied":True,"embedding_batches":(len(needed)+15)//16}


def status(settings):
    path = database_path(settings)
    with connect(path) as db:
        state,meta = snapshot(db,settings)
    return {"database":str(path),"metadata":meta,"state_sha256":digest(state),
            "documents":[{"id":d[0],"title":d[1],"version":d[2],"filename":d[4],"sha256":d[5],
                          "chunks":sum(p[1]==d[0] for p in state["chunks"])} for d in state["documents"]]}


def recover(settings):
    """Let SQLite recover its own hot journal; never reconstruct or repair rows.

    Read-only answering cannot write a rollback journal recovery. This explicit
    local command opens a writer, takes a bounded lock and verifies the recovered
    generation. Failure is surfaced; no auto retry/rebuild/FTS repair occurs.
    """
    path=database_path(settings)
    with connect(path,readonly=False) as db:
        db.execute("BEGIN IMMEDIATE")
        state,meta=snapshot(db,settings)
        db.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('integrity-check')")
    return {"sqlite_recovery_checked":True,"state_sha256":digest(state),"index_revision":meta.get("index_revision","0")}
