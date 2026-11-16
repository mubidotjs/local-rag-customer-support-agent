"""Reproducible synthetic lifecycle acceptance. Never constructs or contacts Ollama."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
from support_agent.answer import answer
from support_agent.config import Settings
from support_agent.embeddings import SyntheticEmbeddings
from support_agent.integrity import checked_index
from support_agent.lifecycle import apply, preview, status
from support_agent.retrieval import retrieve
from support_agent.store import connect, ingest
from evaluation.relevance.offline import preserved


ROOT=Path(__file__).resolve().parents[1]
EXTRA=["evaluation/relevance/holdout.json","evaluation/relevance/freeze.json","evaluation/relevance/preservation.json",
       *["reports/offline-relevance-hardening/"+name for name in ("audit.json","report.md","replay.json","holdout.json")]]


def verify_preservation(require_local=False):
    original=preserved(require_local=require_local)
    pinned=json.loads((ROOT/"reports/document-lifecycle/preservation.json").read_text())
    for name,expected in pinned.items():
        if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=expected:
            raise ValueError("Prior offline evidence changed: "+name)
    return {"historical_manifest_entries":original,"prior_offline_artifacts_verified":len(pinned),
            "ignored_local_entries_available":sum((ROOT/name).exists() for name in json.loads((ROOT/"evaluation/relevance/preservation.json").read_text())["optional_in_fresh_ci"])}


def run_synthetic():
    preservation=verify_preservation()
    with tempfile.TemporaryDirectory(prefix="rag-lifecycle-synthetic-") as tmp:
        root=Path(tmp)/"invented"; root.mkdir()
        settings=replace(Settings(),db_path=Path(tmp)/"synthetic.sqlite3",managed_embeddings=False)
        adapter=SyntheticEmbeddings(settings)
        def write(identifier,body,version="1",title=None):
            (root/(identifier+".md")).write_text(f'---\nid: {identifier}\ntitle: {title or identifier.title()+" Note"}\nversion: {version}\ntopic: {identifier}\n---\n{body}\n',encoding="utf-8",newline="\n")
        write("vesper","The vesper token remains valid for 26 minutes.")
        write("marlow","The marlow token remains valid for 38 minutes.")
        initial=ingest(root,settings,adapter)
        records=[]
        def change(name,operation,identifier=None):
            options={"document_id":identifier} if identifier else {}
            if operation in {"add","update"}: options.update(directory=root,filename=(identifier or "tamar")+".md")
            if operation!="delete": options["binding"]=adapter.binding
            calls=adapter.calls
            p=preview(settings,operation,**options)
            result=apply(settings,p,adapter,confirmation=p["confirmation"])
            with connect(settings.db_path) as db: checked_index(db,settings,allow_empty=True)
            records.append({"case":name,**{k:result[k] for k in ("vectors_reused","vectors_required","unchanged_chunk_rows","chunks_after","applied")},
                            "synthetic_embedding_batches":adapter.calls-calls,"fts_and_source_integrity":True})
        change("unchanged reindex","reindex")
        write("tamar","The tamar token remains valid for 44 minutes.")
        change("add","add")
        write("tamar","The tamar token remains valid for 45 minutes.","2")
        change("content update","update","tamar")
        write("tamar","The tamar token remains valid for 45 minutes.","2.1","Expanded Tamar Support Note")
        change("metadata and offset update","update","tamar")
        first="D"*599+"."; second="E"*599+"."; third="F"*599+"."
        write("tamar",first+"\n\n"+second,"3")
        change("two replacement chunks","update","tamar")
        write("tamar",third+"\n\n"+first+"\n\n"+second,"4")
        change("boundary/ordinal shift","update","tamar")
        change("delete","delete","tamar")
        before=settings.db_path.read_bytes()
        selection={"abstain":False,"claims":[{"chunk_id":"vesper:c000","quote":"The vesper token remains valid for 26 minutes."}]}
        adapter.select=lambda *_:selection
        result=answer("How long does the vesper token remain valid?",settings,adapter)
        assert not result["abstained"] and settings.db_path.read_bytes()==before
        assert not any(p["document_id"]=="tamar" for p in retrieve("tamar token",settings,adapter))
        # Embedding failure leaves the last committed generation byte-for-byte.
        write("tamar","The tamar token remains valid for 46 minutes.")
        p=preview(settings,"add",directory=root,filename="tamar.md",binding=adapter.binding)
        original=adapter.embed
        adapter.embed=lambda *_:(_ for _ in ()).throw(RuntimeError("synthetic embedding failure"))
        try: apply(settings,p,adapter,confirmation=p["confirmation"])
        except RuntimeError: pass
        else: raise AssertionError("Expected synthetic failure")
        adapter.embed=original
        assert settings.db_path.read_bytes()==before
        final_documents=[d["id"] for d in status(settings)["documents"]]
    verify_preservation()
    return {"kind":"offline synthetic document lifecycle acceptance; not live embedding acceptance",
            "baseline_commit":"4d10e3b59a55dce71a5252f78939882f75b0ad20","real_model_status":"NOT RUN",
            "real_requests":{"embedding":0,"generation":0,"metadata":0,"download":0},
            "preservation":preservation,"initial_documents":initial["documents"],"initial_chunks":initial["chunks"],
            "operations":records,"read_only_answer_citation_valid":True,"deleted_document_not_retrieved":True,
            "embedding_exception_preserved_previous_index":True,"final_documents":final_documents}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args()
    result=run_synthetic()
    if args.output:
        with args.output.open("x",encoding="utf-8",newline="\n") as stream:
            json.dump(result,stream,indent=2); stream.write("\n")
    else: print(json.dumps(result,indent=2))
