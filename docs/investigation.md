# Milestone 4A: local investigation

This optional entry point runs a real LangGraph state machine through an owned,
read-only MCP server. Its planner and answer selector are deterministic Python.
It makes **no model requests**. It is an offline workflow acceptance, not evidence
of new live AI capability or an independently reviewed benchmark.

The existing FastAPI `/api/ask`, React UI, ingestion commands and dependency lock
are unchanged. They run without the optional packages. There is no new HTTP or
administrative endpoint.

## Install and run

Use Python 3.12 or 3.13 in a separate virtual environment:

```powershell
python -m venv .local/investigation-venv
.local/investigation-venv/Scripts/python -m pip install -r requirements-investigation.lock
$env:PYTHONUTF8 = '1'
.local/investigation-venv/Scripts/python -m support_agent.investigation --demo
```

The demo creates and removes a temporary fictional index with deterministic fake
vectors. It prints an exact quoted answer, source offsets, relevance proofs and a
trace. It does not open a historical index. On Linux use `bin/python` instead of
`Scripts/python`.

For an owner-selected **synthetic-bound** index:

```text
python -m support_agent.investigation --db fictional.sqlite3 --synthetic --question "How long does the harbor access token remain valid?"
python -m support_agent.investigation --db fictional.sqlite3 --synthetic --serve-stdio
```

For read-only metadata inspection of an existing compatible index:

```text
python -m support_agent.investigation --db owner-selected.sqlite3 --serve-stdio
```

The last command provides local MCP stdio, with no listener or external server
discovery. Its search tool refuses a real embedding-bound index: Milestone 4A
contains no live query adapter. `--synthetic` cannot bypass that refusal; the exact
synthetic binding, preprocessing configuration and eight-dimensional index must
match. No command downloads or substitutes a model. A server instance has a
four-attempt tool budget; restart is an explicit owner action.

Install the optional lock only when using this feature. The base Windows Python
3.15 environment could not install the SDK's `zstandard` dependency without a
compiler/wheel; the isolated local acceptance used Python 3.13.18. CI tests Python
3.12 and 3.13. The existing base environment remains usable.

## Workflow and trace

The typed graph has these explicit nodes:

1. `validate_request`: strict question schema and instruction detection.
2. `investigation_plan`: recognize the existing bounded grounding intents.
3. `tool_selection`: select the next exact allowlisted call and arguments.
4. `tool_execution`: invoke the official MCP client/server in memory.
5. `evidence_assembly`: reconcile versions, source chunks and provenance.
6. `grounding_validation`: validate every exact claim using the existing validator.
7. `final_response`: return the validated quotes or explicit abstention.

Selection/execution repeats only for the fixed three-call plan: `search_passages`,
`inspect_versions`, `citation_metadata`. A normal supported investigation takes
12 node executions and three tool calls. Failed nodes route to abstention; a
depleted budget or cancellation terminates immediately without another node.
There are no model-generated plans, recursive tools or automatic retries.

Each trace event records step, state and outcome. Tool events also carry tool
name and canonical argument/result SHA-256 values when available. Diagnostics
include a logical index stamp, negotiated MCP version, counters and `model_calls: 0`.
No secrets, raw exceptions or wall-clock timestamps enter deterministic traces.
The same input and index produce the same successful trace; elapsed-time failures
are intentionally dependent on execution conditions.

See [four synthetic traces](../reports/investigation-offline/examples.json) and
the [offline acceptance report](../reports/investigation-offline/report.md).
The examples cover a supported answer, missing evidence, document instruction
quarantine and conflicting versions. Historical grounding labels/results are not
rewritten or rescored by these examples.

## Dependencies and verification

`requirements-investigation.lock` pins the optional stack separately:
LangGraph **1.2.22**, official Python MCP SDK **2.3.0**, `mcp-types` **2.3.0**,
and coverage **7.17.3**, plus their exact dependencies and the original base lock.
The local protocol negotiation recorded in examples is **2026-07-28**. SDK tool
annotations are descriptive hints; application schemas and read-only connections
enforce permissions.

Primary references: [LangGraph graph API](https://docs.langchain.com/oss/python/langgraph/graph-api),
[graph control flow](https://docs.langchain.com/oss/python/langgraph/graph-api-control-flow),
[official MCP SDK](https://github.com/modelcontextprotocol/python-sdk), and
[MCP SDK API](https://py.sdk.modelcontextprotocol.io/).

```powershell
$env:RUN_REAL_OLLAMA = '0'
$env:RAG_REAL_E2E = '0'
$env:PYTHONUTF8 = '1'
$env:COVERAGE_FILE = '.local/investigation.coverage'
python -m coverage run --branch --source=support_agent.investigation -m pytest -q
python -m coverage report --fail-under=90
python -m evaluation.investigation_offline --preservation-only
```

`Offline investigation` CI runs the full backend suite with optional dependencies.
The original CI independently tests the application without them, plus the
existing UI build, Vitest and synthetic Playwright checks. Live variants remain
disabled. Test fixtures use temporary invented indexes; focused investigation
tests block socket connections.

Security boundaries and limitations are in
[investigation-security.md](investigation-security.md). The proposed
[Milestone 4B budget](../reports/investigation-offline/proposed-4b-budget.json) is
**not authorized or executed**.
