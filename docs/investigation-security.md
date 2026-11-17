# Investigation architecture and tool permissions

## Trust boundary

The owner selects a single SQLite index at CLI startup. Requests contain a
question only. Tools accept strict structured arguments; tool data cannot select
another database, adapter, server, prompt, executable or network destination.
There is no public investigation API and no externally discovered MCP service.

The LangGraph planner chooses a fixed finite investigation plan. The official MCP
SDK carries the calls to an owned server object in memory; an optional local
stdio server exposes the same five capabilities. It advertises tools only, not
prompts, resources, sampling or client-side execution capabilities. The client
has no cache, automatic input-required rounds or retry policy. There is no model
or generative semantic judge in this milestone.

## Permission matrix

All five tools are read-only. IDs must match the application's finite ID grammar;
unknown fields, type coercion, duplicate IDs, excessive arguments and unknown
tools are rejected before SQL use.

| Tool | Allowed arguments | Bounded output | Refused capabilities |
| --- | --- | --- | --- |
| `list_documents` | limit 1–10, offset 0–49 | Metadata page; total at most 50 | Paths, SQL, source mutation |
| `inspect_document` | Indexed document ID; chunk limit 1–6 | Metadata, exact chunks, truncation flag | Filenames, arbitrary file reads |
| `search_passages` | Question 3–600 characters; limit 1–6 | Existing cosine/FTS5/RRF rankings, chunks and scores | Live embeddings, URLs, remote search |
| `inspect_versions` | 1–6 unique indexed document IDs | At most 50 metadata rows sharing those topics; version/conflict flags | Selecting authoritative versions or changing labels |
| `citation_metadata` | 1–6 unique exact chunk IDs | Exact complete sentence quotes and original/normalized offsets | Invented citations, repaired or inferred quotations |

No tool accepts raw SQL, filesystem paths, shell commands, browser URLs, arbitrary
Python or another tool call. There are no write, ingestion, deletion, messaging,
network-fetch or production-action tools. Read-only/idempotent/closed-world MCP
annotations describe these capabilities; they do not grant authority by
themselves. Unknown protocol methods do not become application tools.

## Storage, evidence and grounding

Every tool opens `store.connect(..., readonly=True)`: SQLite URI `mode=ro`,
`PRAGMA query_only=ON`, `trusted_schema=OFF`, and an explicit read transaction.
The existing path validation rejects symlinks and hardlinked indexes. Bound
parameters carry selected document IDs and the quoted FTS expression. Dynamic
placeholder counts and the busy-timeout integer are computed by trusted code.

`checked_index` verifies exact schema, finite vectors/dimensions, source hashes,
document metadata, deterministic chunk boundaries, sentence offsets and FTS5
correspondence before use. Document-wide instruction quarantine and conservative
topic/version ambiguity flags are retained. No data is written during answering.

Hybrid retrieval uses the existing cosine/FTS5/equal-weight reciprocal-rank-fusion
algorithm, with `k=60`, canonical duplicate text, ID tie-breaking and context caps.
The new tool implements that algorithm on its own deadline-bound verified
snapshot, rather than changing the historical retrieval function. Regression
tests compare the two rankings, including duplicate content and the top-20 cap.
Only the explicit digest/configuration-bound `SyntheticEmbeddings` adapter is
available for queries here. Real-index metadata inspection works without model
access; real-index passage search fails closed.

A logical SHA-256 stamp includes index metadata, document hashes/versions and
stored chunk rows/vectors. The first successful snapshot pins the session. A
changed snapshot stops the investigation. This is optimistic version checking,
not a long-lived lock across all tools; the response identifies the snapshot it
validated. An update after final validation can make a response historical.

The owned runtime retains an out-of-band issuance record for each bounded call.
The graph verifies the exact call/result pair, expected tool and stamp, strict
output schema, and canonical chunks from a fresh verified snapshot. A result
cannot self-attest provenance by supplying an invented hash. Fabricated quotes,
offsets, IDs, scores or version flags are rejected; conflicting tool results fail
closed. This mechanism deliberately trusts only the local implementation, not an
arbitrary remote server with the same schema.

The existing `verify_selection` remains the final claim validator: complete exact
sentences, retrieved chunk membership, original offsets, entity/facet relevance,
explicit alias evidence and complete compound-question support. Invalid extra
claims reject the entire selection; no semantic repair or answer rewriting occurs.
The selector preserves explicit procedure steps and abstains on multiple action
roots. Retrieved instructions remain untrusted data and cannot influence tool
selection. Question instructions, source quarantine, missing support, ambiguous
versions and conflicts lead to abstention.

## Finite execution

| Boundary | Default | Hard schema ceiling |
| --- | --- | --- |
| Graph node executions | 20 | 32 |
| Tool attempts, including invalid/failed calls | 4 | 6 |
| Overall graph deadline | 10 seconds | 30 seconds |
| Per-tool deadline | 2 seconds | 5 seconds |
| Canonical argument bytes | 4,096 | 4,096 |
| Raw stdio JSON-RPC line, before SDK parsing | 8,192 bytes | 8,192 bytes |
| Serialized tool result | 64,000 bytes | 64,000 bytes |
| Retrieved chunks / context | 6 / 4,800 characters | Also bounded by base settings |
| Final exact quoted claims | 3 | 3 |

Inherited default index caps are 50 documents, 500 chunks, 64,000 bytes per source file,
800 characters per chunk and 64,000,000 bytes per database. Existing base validators enforce
them. A stdio server's tool-attempt budget lasts for that server instance, not per
untrusted request. Its owner must explicitly start a fresh instance to reset it.

Independent counters supplement LangGraph's recursion limit. There are no graph
cycles except the finite three-tool sequence, no nested tools and no automatic
retry. Cancellation/overall timeout return abstention; exhausted budgets do not
run extra cleanup graph nodes. SQLite progress checks interrupt native queries;
busy waits are at most 100 ms and obey the remaining deadline. Async deadlines
and cancellation are cooperative: an OS stall or uninterruptible Python/C code
cannot be given a hard real-time kill guarantee. This bounded local implementation
does not create detached tool workers or background inference.

## Locality, optionality and remaining limits

Cloud tracing configuration is refused before tool access. LangSmith tracing is
also disabled in a scoped context even under inherited programmatic tracing;
graph callbacks are empty and MCP server telemetry middleware is removed. No
secrets are copied into diagnostics. The SDK's transitive networking/auth packages
do not grant tools network capabilities. No provider keys are required.

The original `/api/ask`, host/origin restrictions and UI are unchanged. Optional
imports occur only under the separate CLI/module. Tests explicitly block the new
SDK imports while exercising the existing API. Unavailable optional dependencies
give a clear CLI error instead of changing base application behavior.

Limitations: intent/alias/action handling remains a finite English grammar;
unsupported paraphrases may abstain. Conservative topic/version checks can reject
agreeing documents with different signatures. Exact provenance is not proof that
owner-authored facts are true. Coherent local replacement of the corpus and all
hashes remains outside internal-corruption detection. Synthetic vector success
does not establish live semantic retrieval quality or future model acceptance.
There is no live query adapter in Milestone 4A, LLM planner, unrestricted semantic
judge, external MCP integration or persistent agent memory.

The SHA-pinned preservation inventory covers 293 predecessor/historical files,
including all 233 previously protected artifacts and consumed claims/final index.
Fresh CI may lack only the 27 explicitly listed ignored local artifacts; local
delivery verification requires every one. Existing application and historical
files were not modified. The new tests use fictional temporary databases.
