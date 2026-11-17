"""Opt-in local CLI; no new API endpoint and no real model adapter."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from ..config import Settings
from ..embeddings import SyntheticEmbeddings
from ..store import ingest


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db",type=Path,help="Owner-selected existing local index; read-only")
    source.add_argument("--demo",action="store_true",help="Create and remove a temporary fictional synthetic index; no model calls")
    parser.add_argument("--question",default="How long does the harbor access token remain valid?")
    parser.add_argument("--synthetic",action="store_true",help="Enable fake query vectors only for an exact synthetic-bound index")
    parser.add_argument("--serve-stdio",action="store_true",help="Serve only the five read-only MCP tools over stdio; no HTTP listener")
    args=parser.parse_args(argv)
    try:
        from .tools import ReadOnlyTools
        from .workflow import investigate,tracing_guard
        from .mcp_server import serve_stdio
        tracing_guard()
        def execute(settings,synthetic):
            runtime=ReadOnlyTools(settings,synthetic=synthetic)
            if args.serve_stdio:
                asyncio.run(serve_stdio(runtime))
            else:
                print(json.dumps(asyncio.run(investigate({"question":args.question},runtime)),indent=2))
        if args.demo:
            if args.serve_stdio: parser.error("The temporary demo is an investigation, not a persistent server")
            with tempfile.TemporaryDirectory(prefix="project5-investigation-synthetic-") as tmp:
                root=Path(tmp); sources=root/"invented"; sources.mkdir()
                (sources/"harbor.md").write_text("---\nid: harbor\ntitle: Harbor Access Guide\nversion: 1\ntopic: harbor\n---\nThe harbor access token remains valid for 16 minutes.\n",encoding="utf-8",newline="\n")
                settings=replace(Settings(),db_path=root/"synthetic.sqlite3",managed_embeddings=False)
                ingest(sources,settings,SyntheticEmbeddings(settings))
                execute(settings,True)
        else:
            execute(replace(Settings(),db_path=args.db),args.synthetic)
    except ImportError as exc:
        parser.exit(2,"Investigation dependencies unavailable. Install requirements-investigation.lock; the base /api/ask remains independent.\n")
    except (RuntimeError,ValueError,OSError) as exc:
        parser.exit(2,"Local investigation unavailable; check the read-only index and documented limits.\n")


if __name__=="__main__": main()
