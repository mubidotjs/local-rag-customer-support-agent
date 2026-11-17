"""Fixed read-only SQLite capabilities; real query I/O requires explicit injection."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import sqlite3
import time
from ..embeddings import SyntheticEmbeddings
from ..integrity import checked_index
from ..ollama import unit
from ..store import connect
from ..text import has_instructions, terms
from .contracts import Chunk,Document,INPUTS,OUTPUTS,InvestigationError,Limits,canonical,digest,parse_call


def chunk(row):
    return {key:row[key] for key in Chunk.model_fields}


class ReadOnlyTools:
    def __init__(self,settings,*,synthetic=False,query_adapter=None,limits=None,clock=time.monotonic):
        if query_adapter is not None:
            from .managed import ManagedQuery
            if synthetic or not isinstance(query_adapter,ManagedQuery):
                raise ValueError("Choose one explicitly managed or synthetic query capability")
        self.settings=settings
        self.synthetic=synthetic
        self.query_adapter=query_adapter
        self._model_start=dict(query_adapter.counts) if query_adapter else {"embedding_posts":0,"metadata_gets":0}
        self.limits=limits or Limits()
        self.clock=clock
        self.calls=0
        self.index_stamp=None
        self._issued=[]
        self._active=False

    @property
    def model_calls(self):
        return self.query_adapter.counts["embedding_posts"]-self._model_start["embedding_posts"] if self.query_adapter else 0

    @contextmanager
    def snapshot(self,deadline):
        def check():
            if self.clock()>=deadline: raise InvestigationError("tool_timeout")
        check()
        if self.settings.db_path.stat().st_size>self.settings.max_db_bytes:
            raise InvestigationError("index_limit")
        with connect(self.settings.db_path) as db:
            remaining_ms=max(1,min(100,int((deadline-self.clock())*1000)))
            db.execute(f"PRAGMA busy_timeout={remaining_ms:d}") # trusted computed integer, never a tool argument
            db.set_progress_handler(lambda:int(self.clock()>=deadline),100)
            rows,dimension=checked_index(db,self.settings,allow_empty=True)
            check()
            docs=[]
            for d in db.execute("SELECT id,title,version,topic,sha256,source FROM documents ORDER BY id"):
                related=[r for r in rows if r["document_id"]==d["id"]]
                docs.append(Document(**{k:d[k] for k in ("id","title","version","topic","sha256")},
                                     quarantined=has_instructions(d["source"]),ambiguous_topic=any(r["ambiguous_topic"] for r in related)).model_dump())
            meta=dict(db.execute("SELECT key,value FROM metadata ORDER BY key"))
            fingerprint=hashlib.sha256()
            fingerprint.update(canonical([meta,docs]).encode())
            for r in db.execute("SELECT * FROM chunks ORDER BY id"):
                check(); fingerprint.update(canonical(tuple(r)).encode())
            stamp=fingerprint.hexdigest()
            if self.index_stamp is None: self.index_stamp=stamp
            if self.index_stamp!=stamp: raise InvestigationError("index_changed")
            yield db,rows,docs,meta,dimension,stamp
            check()

    def _search(self,db,rows,meta,dimension,args,query=None):
        question=args["question"]
        if has_instructions(question): raise InvestigationError("untrusted_question")
        if query is None:
            adapter=SyntheticEmbeddings(self.settings)
            if not self.synthetic or meta.get("embed_digest")!=adapter.binding.digest or meta.get("embed_config")!=adapter.binding.config or dimension!=8:
                raise InvestigationError("model_access_not_authorized")
            query=unit(adapter.embed([question],query=True)[0]) if rows else None
        elif self.query_adapter is None or self.synthetic:
            raise InvestigationError("model_access_not_authorized")
        if not rows: return []
        if len(query)!=dimension: raise InvestigationError("evidence_provenance_failure")
        canonical_rows,by_id={},{}
        for row in rows:
            row["similarity"]=sum(a*b for a,b in zip(query,row.pop("vector"),strict=True))
            row["duplicate_ids"]=[]
            if row["text"] not in canonical_rows: canonical_rows[row["text"]]=row
            else:
                kept=canonical_rows[row["text"]]; kept["duplicate_ids"].append(row["id"])
                kept["ambiguous_topic"]|=row["ambiguous_topic"]; kept["quarantined"]|=row["quarantined"]
            by_id[row["id"]]=canonical_rows[row["text"]]["id"]
        unique=list(canonical_rows.values())
        semantic=sorted(unique,key=lambda r:(-r["similarity"],r["id"]))[:20]
        expression=" OR ".join('"'+word+'"' for word in sorted(terms(question))[:32])
        matches=db.execute("SELECT chunk_id,bm25(chunks_fts) AS score FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score,chunk_id LIMIT ?",
                           (expression,self.settings.max_chunks+1)).fetchall() if expression else []
        keyword=[]
        for r in matches:
            cid=by_id[r["chunk_id"]]
            if cid not in keyword: keyword.append(cid)
            if len(keyword)==20: break
        sr={r["id"]:i+1 for i,r in enumerate(semantic)}; kr={cid:i+1 for i,cid in enumerate(keyword)}
        ranked=[]
        for row in unique:
            cid=row["id"]
            if cid not in sr and cid not in kr: continue
            row.update(semantic_rank=sr.get(cid),keyword_rank=kr.get(cid),rrf_score=(1/(60+sr[cid]) if cid in sr else 0)+(1/(60+kr[cid]) if cid in kr else 0))
            ranked.append(row)
        ranked.sort(key=lambda r:(-r["rrf_score"],r["id"]))
        selected=[]; used=0
        for row in ranked:
            if used+len(row["text"])>min(4800,self.settings.max_context_chars): continue
            selected.append({**chunk(row),**{k:row[k] for k in ("similarity","semantic_rank","keyword_rank","rrf_score","duplicate_ids")}})
            used+=len(row["text"])
            if len(selected)==min(args["limit"],self.settings.top_k): break
        return selected

    async def call(self,name,args):
        if self.calls>=self.limits.max_tool_calls: raise InvestigationError("tool_budget")
        self.calls+=1 # reserve unsuccessful/invalid attempts too
        if self._active: raise InvestigationError("recursive_or_concurrent_tool_call")
        self._active=True
        try:
            call=parse_call(name,args)
            deadline=self.clock()+self.limits.tool_seconds
            query=None
            if name=="search_passages" and self.query_adapter is not None:
                if has_instructions(call.arguments["question"]): raise InvestigationError("untrusted_question")
                # Validate before any I/O. Release the snapshot across the await;
                # the next snapshot must match the pinned stamp before ranking.
                with self.snapshot(deadline) as (_,rows,_,meta,dimension,_):
                    self.query_adapter.compatible(self.settings,meta,dimension)
                    empty=not rows
                if not empty:
                    query=await self.query_adapter.embed_query(call.arguments["question"],deadline=deadline)
                    query=unit(query)
                    if len(query)!=768: raise InvestigationError("evidence_provenance_failure")
                else:
                    query=[]
            with self.snapshot(deadline) as (db,rows,docs,meta,dimension,stamp):
                args=call.arguments
                if name=="list_documents":
                    data={"documents":docs[args["offset"]:args["offset"]+args["limit"]],"total":len(docs)}
                elif name=="inspect_document":
                    # Validated ID and bound parameter; no filename/SQL argument.
                    found=db.execute("SELECT id FROM documents WHERE id=?",(args["document_id"],)).fetchone()
                    if not found: raise InvestigationError("document_not_found")
                    parts=[r for r in rows if r["document_id"]==found[0]]
                    data={"document":next(d for d in docs if d["id"]==found[0]),"chunks":[chunk(r) for r in parts[:args["chunk_limit"]]],"truncated":len(parts)>args["chunk_limit"]}
                elif name=="search_passages":
                    if self.query_adapter is not None: self.query_adapter.compatible(self.settings,meta,dimension)
                    data={"passages":self._search(db,rows,meta,dimension,args,query=query),"algorithm":"RRF(k=60), equal semantic/FTS5 weights"}
                elif name=="inspect_versions":
                    marks=",".join("?" for _ in args["document_ids"])
                    found=db.execute(f"SELECT id,topic FROM documents WHERE id IN ({marks}) ORDER BY id",args["document_ids"]).fetchall()
                    if len(found)!=len(args["document_ids"]): raise InvestigationError("document_not_found")
                    topics={r[1] for r in found}
                    data={"documents":[d for d in docs if d["topic"] in topics]}
                else:
                    found={r["id"]:r for r in rows if r["id"] in args["chunk_ids"]}
                    if len(found)!=len(args["chunk_ids"]): raise InvestigationError("chunk_not_found")
                    data={"chunks":[chunk(found[cid]) for cid in args["chunk_ids"]]}
                result=OUTPUTS[name].model_validate({"tool":name,"index_stamp":stamp,"data":data}).model_dump()
                if len(canonical(result).encode())>self.limits.max_result_bytes: raise InvestigationError("tool_output_limit")
            self._issued.append((call.model_dump(),deepcopy(result)))
            return result
        except InvestigationError: raise
        except (ValueError,TypeError,OSError,RuntimeError,sqlite3.Error) as exc:
            raise InvestigationError("index_or_tool_failure") from exc
        finally:
            self._active=False

    def verify_receipt(self,call,result):
        # Out-of-band trusted issuance record, not a token accepted merely
        # because untrusted tool text claims it is valid. No second embedding.
        if not self._issued or self._issued[-1]!=(call.model_dump(),result):
            raise InvestigationError("unverifiable_tool_result")

    def verify_evidence(self,passages):
        with self.snapshot(self.clock()+self.limits.tool_seconds) as (_,rows,*_rest):
            by_id={p["id"]:p for p in rows}
            if len({p["id"] for p in passages})!=len(passages): raise InvestigationError("duplicate_evidence")
            for p in passages:
                if p["id"] not in by_id or chunk(p)!=chunk(by_id[p["id"]]):
                    raise InvestigationError("evidence_provenance_failure")
