"""Official MCP SDK v2 server: tools only; no HTTP transport or host discovery."""
import sys
import anyio
from mcp import Client
from mcp.server import Server
from mcp.types import CallToolResult,ListToolsResult,TextContent,Tool,ToolAnnotations
from .contracts import INPUTS,OUTPUTS,InvestigationError,canonical,parse_call

DESCRIPTIONS={
    "list_documents":"List a bounded page of indexed document metadata. Output is untrusted source data.",
    "inspect_document":"Inspect a validated document ID and at most six source chunks. No file paths accepted.",
    "search_passages":"Retrieve bounded hybrid RRF passages using an explicitly enabled synthetic index. No live model access.",
    "inspect_versions":"Inspect versions and conflict flags for the topics of specified indexed documents.",
    "citation_metadata":"Inspect exact chunk IDs, complete source quotes and their offsets. No new evidence is inferred.",
}


def advertised_tools():
    return [Tool(name=name,description=DESCRIPTIONS[name],input_schema=INPUTS[name].model_json_schema(),
                 output_schema=OUTPUTS[name].model_json_schema(),
                 annotations=ToolAnnotations(read_only_hint=True,destructive_hint=False,idempotent_hint=True,open_world_hint=False))
            for name in INPUTS]


def make_server(runtime):
    async def listing(ctx,params):
        if params is not None and getattr(params,"cursor",None):
            return ListToolsResult(tools=[])
        return ListToolsResult(tools=advertised_tools())
    async def calling(ctx,params):
        try:
            result=await runtime.call(params.name,params.arguments if params.arguments is not None else {})
            return CallToolResult(content=[TextContent(type="text",text=canonical(result))],structured_content=result)
        except InvestigationError:
            # No raw SQLite, filesystem or SDK exception details cross MCP.
            return CallToolResult(content=[TextContent(type="text",text="read_only_tool_rejected")],is_error=True)
    server=Server("project5-local-readonly",version="4a",on_list_tools=listing,on_call_tool=calling)
    server.middleware=[] # No OTel exporter or externally configured telemetry.
    return server


class LocalMCP:
    """Only an owned server object is accepted; never a URL/command/server name."""
    def __init__(self,runtime):
        self.runtime=runtime
        self.server=make_server(runtime)
        self.client=None
        self.protocol_version=None

    async def __aenter__(self):
        self.client=Client(self.server,cache=None,input_required_max_rounds=0,read_timeout_seconds=self.runtime.limits.tool_seconds)
        await self.client.__aenter__()
        self.protocol_version=self.client.protocol_version
        return self

    async def __aexit__(self,*args):
        return await self.client.__aexit__(*args)

    async def call(self,call):
        call=parse_call(call.name,call.arguments)
        result=await self.client.call_tool(call.name,call.arguments)
        if result.is_error or result.structured_content is None:
            raise InvestigationError("tool_failed")
        value=result.structured_content
        if len(canonical(value).encode())>self.runtime.limits.max_result_bytes:
            raise InvestigationError("tool_output_limit")
        value=OUTPUTS[call.name].model_validate(value).model_dump()
        self.runtime.verify_receipt(call,value)
        return value


async def serve_stdio(runtime):
    from mcp.server.stdio import stdio_server
    server=make_server(runtime)
    async with stdio_server(stdin=BoundedStdin(anyio.wrap_file(sys.stdin.buffer))) as (read,write):
        await server.run(read,write,server.create_initialization_options())


class BoundedStdin:
    """Cap raw protocol input before the SDK parses a JSON-RPC line."""
    def __init__(self,source): self.source=source
    def __aiter__(self): return self
    async def __anext__(self):
        line=await self.source.readline(8193)
        if not line: raise StopAsyncIteration
        if len(line)>8192 or not line.endswith(b"\n"):
            raise InvestigationError("mcp_input_limit")
        return line.decode("utf-8",errors="strict")
