"""Explicit, finite LangGraph investigation through an owned read-only MCP server."""
import asyncio
from copy import deepcopy
import os
import time
from typing import TypedDict
from ..answer import ABSTENTION,verify_selection
from ..relevance import Grounding
from ..text import clauses_supported,has_instructions,terms
from .contracts import Chunk,INPUTS,OUTPUTS,InvestigationError,Limits,Question,ToolCall,canonical,digest,parse_call
from .mcp_server import LocalMCP

NODES=("validate_request","investigation_plan","tool_selection","tool_execution","evidence_assembly","grounding_validation","final_response")
REASONS={"invalid_request","untrusted_question","unsupported_intent","step_budget","tool_budget","overall_timeout","tool_timeout",
         "tool_failed","invalid_tool_call","unexpected_tool_for_state","tool_output_limit","unverifiable_tool_result","index_changed",
         "index_or_tool_failure","index_limit","evidence_provenance_failure","duplicate_evidence","inconsistent_tool_results",
         "untrusted_source","conflicting_sources","insufficient_evidence","incomplete_evidence","ambiguous_selection","cancelled",
         "external_tracing_forbidden","no_evidence","tool_failure","invalid_selection","verified_exact_quotes"}


class State(TypedDict,total=False):
    request: dict
    question: str
    plan: list[str]
    cursor: int
    pending: dict|None
    passages: list[dict]
    versions: list[dict]
    citation_chunks: list[dict]
    citations: list[dict]
    reason: str
    failed: bool
    steps: int
    tool_calls: int
    trace: list[dict]


def tracing_guard():
    # Do not print or copy secret values. Never install cloud callbacks/exporters.
    switches=("LANGSMITH_TRACING","LANGCHAIN_TRACING_V2","LANGCHAIN_TRACING")
    if any(os.environ.get(k,"").lower() not in {"","0","false","off"} for k in switches) or any(os.environ.get(k) for k in ("LANGSMITH_API_KEY","LANGCHAIN_API_KEY","OTEL_EXPORTER_OTLP_ENDPOINT","OTEL_TRACES_EXPORTER")):
        raise InvestigationError("external_tracing_forbidden")


def public_reason(exc,*,timeout="tool_timeout"):
    if isinstance(exc,BaseExceptionGroup):
        reasons={public_reason(item,timeout=timeout) for item in exc.exceptions}
        return sorted(reasons-{"tool_failure"})[0] if reasons-{"tool_failure"} else "tool_failure"
    if isinstance(exc,asyncio.CancelledError): return "cancelled"
    if isinstance(exc,InvestigationError) and str(exc) in REASONS: return str(exc)
    if isinstance(exc,TimeoutError): return timeout
    return "tool_failure"


def select_exact(question,passages,settings):
    """Finite offline selector, not a model or semantic answer-repair layer."""
    grounding=Grounding(question,passages,settings)
    claims=[]; covered=set(); action_roots={}
    for p in passages:
        for q in p["quotes"]:
            proof,_=grounding.prove(p["id"],q["quote"])
            if not proof: continue
            for item in proof:
                if item["facet"]=="configuration_action" and item["action_link"]!="explicit_procedure_step":
                    roots=action_roots.setdefault(item["intent"],set())
                    roots.add((p["document_id"],q["quote"]))
                    if len(roots)>1: return {"abstain":True,"claims":[]}
            intents={s["intent"] for s in proof}
            procedure=any(s["action_link"]=="explicit_procedure_step" for s in proof)
            if intents-covered or procedure:
                claims.append({"chunk_id":p["id"],"quote":q["quote"]}); covered|=intents
    if len(claims)>3: return {"abstain":True,"claims":[]}
    return {"abstain":not claims,"claims":claims}


async def investigate(request,runtime,*,limits=None,selector=None,chooser=None,transport=None,cancel=None,clock=time.monotonic):
    """Trusted code may inject test stubs; request/tool data never supplies hooks."""
    limits=limits or runtime.limits
    if limits!=runtime.limits: raise ValueError("Runtime and graph budgets must match")
    started=clock(); trace=[]; current={"steps":0,"tool_calls":0}; protocol=None
    state:State={"request":request,"question":"","plan":[],"cursor":0,"pending":None,"passages":[],"versions":[],
                 "citation_chunks":[],"citations":[],"reason":"insufficient_evidence","failed":False,"steps":0,"tool_calls":0,"trace":[]}
    def check():
        if cancel is not None and cancel.is_set(): raise InvestigationError("cancelled")
        if clock()-started>=limits.total_seconds: raise InvestigationError("overall_timeout")
    def result(reason,citations=None):
        citations=citations or []
        return {"answer":"\n\n".join(f'{c["quote"]} [{c["chunk_id"]}]' for c in citations) if citations else ABSTENTION,
                "abstained":not bool(citations),"citations":citations,"trace":deepcopy(trace),
                "diagnostics":{"reason":reason,"steps":current["steps"],"tool_calls":current["tool_calls"],
                               "index_stamp":runtime.index_stamp,"model_calls":runtime.model_calls,"mcp_protocol":protocol,
                               "mode":"local-managed-query" if runtime.query_adapter else "offline-synthetic" if runtime.synthetic else "metadata-only-no-model"}}
    async def validate(s):
        try:
            if len(canonical(s["request"]).encode())>4096: raise ValueError()
            q=Question.model_validate(s["request"]).question
        except (ValueError,TypeError,RecursionError): raise InvestigationError("invalid_request")
        if has_instructions(q): raise InvestigationError("untrusted_question")
        return {"question":q}
    async def plan(s):
        if any(i.facet=="unknown" for i in Grounding(s["question"],[],runtime.settings).intents):
            raise InvestigationError("unsupported_intent")
        return {"plan":["search_passages","inspect_versions","citation_metadata"]}
    def expected_call(s):
        name=s["plan"][s["cursor"]]
        if name=="search_passages": args={"question":s["question"],"limit":6}
        elif name=="inspect_versions": args={"document_ids":sorted({p["document_id"] for p in s["passages"]})}
        else: args={"chunk_ids":[p["id"] for p in s["passages"]]}
        return parse_call(name,args)
    async def select(s):
        if s["cursor"]==len(s["plan"]): return {"pending":None}
        expected=expected_call(s)
        chosen=selector(deepcopy(s)) if selector else expected.model_dump()
        try: chosen=ToolCall.model_validate(chosen)
        except (ValueError,TypeError): raise InvestigationError("invalid_tool_call")
        chosen=parse_call(chosen.name,chosen.arguments)
        if chosen!=expected: raise InvestigationError("unexpected_tool_for_state")
        return {"pending":chosen.model_dump()}
    async def execute(s):
        if current["tool_calls"]>=limits.max_tool_calls: raise InvestigationError("tool_budget")
        current["tool_calls"]+=1
        call=ToolCall.model_validate(s["pending"])
        before=runtime.calls
        async with asyncio.timeout(limits.tool_seconds):
            value=await channel.call(call)
        check()
        if runtime.calls!=before+1: raise InvestigationError("unverifiable_tool_result")
        if len(canonical(value).encode())>limits.max_result_bytes: raise InvestigationError("tool_output_limit")
        value=OUTPUTS[call.name].model_validate(value).model_dump()
        runtime.verify_receipt(call,value)
        if value["tool"]!=call.name or value["index_stamp"]!=runtime.index_stamp: raise InvestigationError("inconsistent_tool_results")
        update={"cursor":s["cursor"]+1,"tool_calls":current["tool_calls"]}
        if call.name=="search_passages":
            update["passages"]=value["data"]["passages"]
            if not update["passages"]: raise InvestigationError("no_evidence")
        elif call.name=="inspect_versions": update["versions"]=value["data"]["documents"]
        else: update["citation_chunks"]=value["data"]["chunks"]
        update["_tool_hash"]=digest(value)
        return update
    async def assemble(s):
        if [chunk for chunk in s["citation_chunks"]]!=[{k:p[k] for k in Chunk.model_fields} for p in s["passages"]]:
            raise InvestigationError("inconsistent_tool_results")
        runtime.verify_evidence(s["passages"])
        grounding=Grounding(s["question"],s["passages"],runtime.settings)
        relevant=[p for p in s["passages"] if p["similarity"]>=runtime.settings.min_similarity and
                  (terms(s["question"])&terms(p["text"]) or any(grounding.prove(p["id"],q["quote"])[0] for q in p["quotes"]))]
        if any(p["quarantined"] for p in relevant): raise InvestigationError("untrusted_source")
        if any(p["ambiguous_topic"] for p in relevant) or any(d["ambiguous_topic"] for d in s["versions"] if d["id"] in {p["document_id"] for p in relevant}):
            raise InvestigationError("conflicting_sources")
        if not relevant: raise InvestigationError("insufficient_evidence")
        if not clauses_supported(s["question"],[q["quote"] for p in relevant for q in p["quotes"]]): raise InvestigationError("incomplete_evidence")
        return {}
    async def ground(s):
        selection=chooser(s["question"],deepcopy(s["passages"])) if chooser else select_exact(s["question"],s["passages"],runtime.settings)
        citations,reason=verify_selection(selection,s["passages"],s["question"],runtime.settings)
        if not citations: raise InvestigationError("invalid_selection")
        runtime.verify_evidence(s["passages"])
        return {"citations":citations,"reason":reason}
    async def final(s):
        return {}
    functions=dict(zip(NODES,(validate,plan,select,execute,assemble,ground,final),strict=True))
    def wrap(name):
        async def node(s):
            check()
            if current["steps"]>=limits.max_steps: raise InvestigationError("step_budget")
            current["steps"]+=1
            event={"step":current["steps"],"state":name,"outcome":"ok"}
            if name=="tool_execution" and s["pending"]:
                event.update(tool=s["pending"]["name"],arguments_sha256=digest(s["pending"]["arguments"]))
            try:
                update=await functions[name](s)
                if "_tool_hash" in update:
                    event.update(tool=s["pending"]["name"],result_sha256=update.pop("_tool_hash"))
                check()
            except asyncio.CancelledError: raise
            except Exception as exc:
                reason=public_reason(exc)
                event.update(outcome="abstain",reason=reason)
                update={"failed":True,"reason":reason,"citations":[]}
            trace.append(event)
            return {**update,"steps":current["steps"],"trace":deepcopy(trace)}
        return node
    try:
        tracing_guard()
        if runtime.calls: raise InvestigationError("tool_budget")
        from langgraph.graph import StateGraph,START,END
        from langsmith import tracing_context
        graph=StateGraph(State)
        for name in NODES: graph.add_node(name,wrap(name))
        graph.add_edge(START,"validate_request")
        for name,next_name in (("validate_request","investigation_plan"),("investigation_plan","tool_selection"),
                               ("tool_execution","tool_selection"),("evidence_assembly","grounding_validation"),("grounding_validation","final_response")):
            graph.add_conditional_edges(name,lambda s,n=next_name:"final_response" if s["failed"] else n)
        graph.add_conditional_edges("tool_selection",lambda s:"final_response" if s["failed"] else "evidence_assembly" if s["pending"] is None else "tool_execution")
        graph.add_edge("final_response",END)
        compiled=graph.compile() # no checkpointer, store, cache, retries or cloud callbacks
        with tracing_context(enabled=False):
            async with asyncio.timeout(limits.total_seconds):
                async with (transport or LocalMCP(runtime)) as channel:
                    protocol=getattr(channel,"protocol_version",None)
                    state=await compiled.ainvoke(state,{"recursion_limit":limits.max_steps+1,"max_concurrency":1,"callbacks":[]})
        return result(state["reason"],[] if state["failed"] else state["citations"])
    except asyncio.CancelledError:
        return result("cancelled")
    except BaseExceptionGroup as exc:
        return result(public_reason(exc,timeout="overall_timeout"))
    except Exception as exc:
        return result(public_reason(exc,timeout="overall_timeout"))
