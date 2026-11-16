"""Bounded local source loading shared by initial ingestion and lifecycle CLI."""
import hashlib
import os
from pathlib import Path
import stat
from .text import HEADER, chunks, normalize


def local_path(path):
    path = Path(path).absolute()
    if str(path).startswith(("\\\\", "//")):
        raise ValueError("Network paths are not local document sources")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Symlinks and junctions are not allowed")
    return path.resolve(strict=True)


def read_document(directory, filename, settings):
    if not isinstance(filename, str) or Path(filename).name != filename or any(c in filename for c in '/\\:') or filename in {"", ".", ".."}:
        raise ValueError("Select one filename directly inside the directory")
    if Path(filename).suffix.lower() not in {".md", ".txt"} or len(filename) > 255:
        raise ValueError("Only Markdown and plain-text filenames are allowed")
    root = local_path(directory)
    if not root.is_dir():
        raise ValueError("Select a local source directory")
    path = local_path(root / filename)
    if path.parent != root:
        raise ValueError("Document path escapes the source directory")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("Only regular, non-hardlinked files are allowed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Source changed while opening")
        raw = stream.read(settings.max_file_bytes + 1)
        after = os.fstat(stream.fileno())
    final = local_path(root / filename).stat()
    # Windows path-stat and handle-stat expose different historical ctime
    # meanings. Compare ctime only within the same stat API, never across them.
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if signature(before) != signature(after) or signature(after) != signature(final) or before.st_ctime_ns != final.st_ctime_ns or opened.st_ctime_ns != after.st_ctime_ns:
        raise ValueError("Source changed while reading")
    if len(raw) > settings.max_file_bytes:
        raise ValueError("Document exceeds byte limit")
    source = raw.decode("utf-8")
    text, offsets = normalize(source)
    match = HEADER.match(text)
    if not match:
        raise ValueError(f"Missing required metadata header: {filename}")
    doc_id, title, version, topic = match.groups()
    doc = (doc_id, title, version, topic, filename, hashlib.sha256(raw).hexdigest(), source, text)
    passages = []
    for ordinal, (a, b, body) in enumerate(chunks(text[match.end():], settings.chunk_chars)):
        a, b = a + match.end(), b + match.end()
        passages.append((f"{doc_id}:c{ordinal:03d}", doc_id, ordinal, a, b, offsets[a][0], offsets[b-1][1], body))
        if len(passages) > settings.max_chunks:
            raise ValueError("Document exceeds chunk limit")
    if not passages:
        raise ValueError("Document body must be nonempty")
    return doc, passages
