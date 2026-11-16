"""Initial ingestion remains compatible; administration exists only in this CLI."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import sys
from .config import SETTINGS
from .documents import local_path
from .embeddings import Binding, ManagedOllama, PINNED_DIGEST, PINNED_MODEL, SyntheticEmbeddings, config_digest
from .lifecycle import apply, preview, recover, status
from .ollama import Ollama
from .store import ingest


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] != "documents":
        parser = argparse.ArgumentParser(description="Initial local ingestion; existing directory invocation is preserved")
        parser.add_argument("directory",type=Path)
        parser.add_argument("--db",type=Path,default=SETTINGS.db_path)
        parser.add_argument("--synthetic",action="store_true",help="Invented test index only; deterministic fake vectors and no model access")
        args = parser.parse_args(argv)
        settings=replace(SETTINGS,db_path=args.db)
        model=SyntheticEmbeddings(settings) if args.synthetic else ManagedOllama(settings) if settings.managed_embeddings else Ollama(settings)
        print(json.dumps(ingest(args.directory,settings,model),indent=2))
        return
    parser = argparse.ArgumentParser(description="Local-only document lifecycle. No model access during preview/status/delete.")
    parser.add_argument("--db",type=Path,default=SETTINGS.db_path)
    parser.add_argument("--chunk-chars",type=int,default=SETTINGS.chunk_chars)
    commands = parser.add_subparsers(dest="command",required=True)
    commands.add_parser("status")
    commands.add_parser("recover",help="Explicit SQLite rollback-journal recovery and validation; no model calls or row repair")
    plan = commands.add_parser("preview")
    plan.add_argument("operation",choices=["add","update","delete","reindex"])
    plan.add_argument("--id",dest="document_id")
    plan.add_argument("--directory",type=Path)
    plan.add_argument("--file",dest="filename")
    plan.add_argument("--plan",type=Path,required=True)
    plan.add_argument("--synthetic",action="store_true",help="Declare deterministic test identity; invented databases only")
    execution = commands.add_parser("apply")
    execution.add_argument("--plan",type=Path,required=True)
    execution.add_argument("--confirm",required=True,help="Exact confirmation token printed by preview")
    group = execution.add_mutually_exclusive_group()
    group.add_argument("--synthetic",action="store_true",help="Use deterministic fake embeddings; never AI acceptance")
    group.add_argument("--allow-local-embedding",action="store_true",help="Future owner-authorized pinned Ollama use; makes metadata and embedding requests")
    args = parser.parse_args(argv[1:])
    settings = replace(SETTINGS,db_path=args.db,chunk_chars=args.chunk_chars)
    try:
        if args.command == "status":
            result = status(settings)
        elif args.command == "recover":
            result = recover(settings)
        elif args.command == "preview":
            binding = None if args.operation == "delete" else SyntheticEmbeddings(settings).binding if args.synthetic else Binding(PINNED_MODEL,PINNED_DIGEST,config_digest(settings))
            result = preview(settings,args.operation,document_id=args.document_id,directory=args.directory,filename=args.filename,binding=binding)
            with args.plan.open("x",encoding="utf-8",newline="\n") as stream:
                json.dump(result,stream,indent=2)
                stream.write("\n")
        else:
            path = local_path(args.plan)
            with path.open("rb") as stream:
                raw = stream.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ValueError("Preview exceeds size limit")
            envelope = json.loads(raw)
            adapter = SyntheticEmbeddings(settings) if args.synthetic else ManagedOllama(settings) if args.allow_local_embedding else None
            result = apply(settings,envelope,adapter,confirmation=args.confirm)
        print(json.dumps(result,indent=2))
    except (ValueError,OSError,RuntimeError,sqlite3.Error) as exc:
        parser.exit(2,f"Document operation rejected: {exc}\n")


if __name__ == "__main__":
    main()
