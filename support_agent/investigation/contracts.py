"""Finite schemas and budgets; tool strings never become code or capabilities."""
import hashlib
import json
from typing import Annotated, Generic, Literal, TypeVar
from pydantic import BaseModel, ConfigDict, Field

ID=Annotated[str,Field(pattern=r"^[a-z0-9-]{1,60}$",max_length=60)]
CID=Annotated[str,Field(pattern=r"^[a-z0-9-]{1,60}:c[0-9]{3}$",max_length=65)]
HASH=Annotated[str,Field(pattern=r"^[0-9a-f]{64}$")]
ToolName=Literal["list_documents","inspect_document","search_passages","inspect_versions","citation_metadata"]


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True,allow_inf_nan=False)


class Question(Strict):
    question: str=Field(min_length=3,max_length=600)


class Limits(Strict):
    max_steps: int=Field(default=20,ge=1,le=32)
    max_tool_calls: int=Field(default=4,ge=0,le=6)
    total_seconds: float=Field(default=10.0,gt=0,le=30)
    tool_seconds: float=Field(default=2.0,gt=0,le=5)
    max_result_bytes: int=Field(default=64000,ge=100,le=64000)


class ListArgs(Strict):
    limit: int=Field(default=10,ge=1,le=10)
    offset: int=Field(default=0,ge=0,le=49)


class InspectArgs(Strict):
    document_id: ID
    chunk_limit: int=Field(default=6,ge=1,le=6)


class SearchArgs(Question):
    limit: int=Field(default=6,ge=1,le=6)


class VersionArgs(Strict):
    document_ids: list[ID]=Field(min_length=1,max_length=6)


class CitationArgs(Strict):
    chunk_ids: list[CID]=Field(min_length=1,max_length=6)


class ToolCall(Strict):
    name: ToolName
    arguments: dict


class Quote(Strict):
    quote: str=Field(min_length=15,max_length=800)
    normalized_start: int=Field(ge=0,le=64000)
    normalized_end: int=Field(ge=1,le=64000)
    source_start: int=Field(ge=0,le=64000)
    source_end: int=Field(ge=1,le=64000)


class Chunk(Strict):
    id: CID
    document_id: ID
    ordinal: int=Field(ge=0,le=499)
    title: str=Field(min_length=1,max_length=120)
    version: str=Field(min_length=1,max_length=40)
    topic: ID
    text: str=Field(min_length=1,max_length=800)
    normalized_start: int=Field(ge=0,le=64000)
    normalized_end: int=Field(ge=1,le=64000)
    source_start: int=Field(ge=0,le=64000)
    source_end: int=Field(ge=1,le=64000)
    quarantined: bool
    ambiguous_topic: bool
    quotes: list[Quote]=Field(max_length=54)


class Passage(Chunk):
    similarity: float=Field(ge=-1.00000001,le=1.00000001)
    semantic_rank: int|None=Field(default=None,ge=1,le=20)
    keyword_rank: int|None=Field(default=None,ge=1,le=20)
    rrf_score: float=Field(ge=0,le=1)
    duplicate_ids: list[CID]=Field(max_length=499)


class Document(Strict):
    id: ID
    title: str=Field(min_length=1,max_length=120)
    version: str=Field(min_length=1,max_length=40)
    topic: ID
    sha256: HASH
    quarantined: bool
    ambiguous_topic: bool


class Listing(Strict):
    documents: list[Document]=Field(max_length=10)
    total: int=Field(ge=0,le=50)


class Inspection(Strict):
    document: Document
    chunks: list[Chunk]=Field(max_length=6)
    truncated: bool


class Search(Strict):
    passages: list[Passage]=Field(max_length=6)
    algorithm: Literal["RRF(k=60), equal semantic/FTS5 weights"]


class Versions(Strict):
    documents: list[Document]=Field(max_length=50)


class Citations(Strict):
    chunks: list[Chunk]=Field(max_length=6)


T=TypeVar("T")
class Envelope(Strict,Generic[T]):
    tool: ToolName
    index_stamp: HASH
    data: T


INPUTS={"list_documents":ListArgs,"inspect_document":InspectArgs,"search_passages":SearchArgs,
        "inspect_versions":VersionArgs,"citation_metadata":CitationArgs}
OUTPUTS={"list_documents":Envelope[Listing],"inspect_document":Envelope[Inspection],"search_passages":Envelope[Search],
         "inspect_versions":Envelope[Versions],"citation_metadata":Envelope[Citations]}


class InvestigationError(RuntimeError):
    """Only a bounded public reason code may cross the protocol boundary."""


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parse_call(name,args):
    try:
        if len(canonical(args).encode())>4096: raise ValueError()
        call=ToolCall(name=name,arguments=args)
        result=INPUTS[call.name].model_validate(args)
        values=result.model_dump()
        if any(isinstance(v,list) and len(set(v))!=len(v) for v in values.values()): raise ValueError()
        return ToolCall(name=call.name,arguments=values)
    except (ValueError,TypeError,RecursionError) as exc:
        raise InvestigationError("invalid_tool_call") from exc
