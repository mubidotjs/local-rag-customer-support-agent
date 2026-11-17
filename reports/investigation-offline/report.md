# Milestone 4A offline acceptance

Source predecessor: `6cfff9488f63809df1310205da1379e475c7b072`.
Delivery identity is the Git commit containing this report. Exact-SHA CI is
published as GitHub checks on that commit, in the original `CI` workflow and the
new `Offline investigation` Python 3.12/3.13 matrix.

**Offline acceptance: PASS. Real model status: NOT RERUN.**
Actual embedding POSTs, generation POSTs, Ollama metadata requests and model
downloads during this milestone: **0 each**. No consumed acceptance was repeated;
no new live claim was consumed. Milestone 4B is not authorized or executed.

## Implemented behavior

The opt-in `python -m support_agent.investigation --demo` entry point executes a
typed LangGraph with explicit validation, planning, tool selection, execution,
evidence assembly, grounding and final response nodes. A supported path completes
in 12 steps and three calls over the official MCP SDK's in-memory transport.
Local stdio exposes the same five strictly read-only capabilities. No HTTP
listener, administrative endpoint or React change was added.

The planner and exact-quote selector are deterministic offline code, not an LLM.
Every final claim goes through the existing citation/entity/facet validator.
Source instructions, conflicts, unsupported entities, incomplete compound answers,
malformed outputs, timeouts and exceeded budgets abstain. Canonical receipts and
fresh verified source snapshots prevent altered tool results from supplying
their own provenance. There is no unsupported-claim repair or label change.

Optional pins: LangGraph 1.2.22, official MCP SDK 2.3.0, mcp-types 2.3.0;
negotiated local protocol 2026-07-28. The original API, requirements, UI, retrieval
and grounding source files are unchanged. The five tools, permissions, finite
limits and usage examples are documented in
[investigation.md](../../docs/investigation.md) and
[investigation-security.md](../../docs/investigation-security.md).

## Offline verification

| Check | Local result |
| --- | --- |
| Full backend suite, optional SDK stack, Python 3.13.18 / UTF-8 | 313 passed, 1 live-inference test skipped |
| Base Python 3.15 environment without LangGraph/MCP | 242 passed, 3 optional/live skips |
| Vitest | 4 passed |
| Production frontend build | PASS |
| Synthetic Playwright | 4 passed, 2 real-model variants skipped |
| Newly added investigation statement coverage | 520 / 526 = 98.86% |
| Newly added investigation branch coverage | 137 / 162 = 84.57% |
| Combined statement/branch coverage | 95.49%; CI gate at 90% combined |
| Strict local historical preservation | 293 / 293 hashes verified; no optional files missing |

[coverage.json](coverage.json) contains per-file statement and branch detail;
[audit.json](audit.json) records pins, verification totals and new source hashes.
Coverage is measured over `support_agent.investigation`, including the CLI, not
over old application code. Uncovered error/defensive branches remain visible;
this is not a claim of complete security coverage.

Tests exercise actual LangGraph and MCP client/server protocol operation with
invented temporary indexes and safe stubs. They cover strict input/output schemas,
SQL/path/shell/URL escalation attempts, read-only enforcement, socket prohibition,
source quarantine, conflicts, altered quotes/IDs/offsets/scores, incomplete answers,
concurrent index changes, bounded SQLite locks, cancellation, tool/step deadlines,
tool failures, tracing suppression and optional-dependency absence. Raw stdio input
is capped before SDK JSON parsing. Hybrid retrieval parity, duplicate handling and
the top-20 keyword cap are checked against the existing retrieval implementation.

## Deterministic demonstration findings

These four fixed-expectation, agent-authored synthetic examples are workflow
checks, **not a human-validated benchmark or new live AI acceptance**.

| Example | Outcome |
| --- | --- |
| Supported Harbor token duration | Exact 16-minute sentence, actual chunk and offsets, relevance proof; no abstention |
| Unsupported visitor-pass duration with Harbor near-match | Abstention; no unsupported answer |
| Document containing instruction injection | Abstention with `untrusted_source` quarantine |
| Two conflicting Harbor versions | Abstention with `conflicting_sources` |

[examples.json](examples.json) contains the complete responses and deterministic
traces. Each database's byte hash stayed unchanged during investigation. Historical
Phase D results, their known defects, frozen labels and earlier replay scores are
preserved as historical evidence; they were not retuned or rescored here.

## Preservation

[preservation.json](preservation.json) pins all 293 predecessor/historical files.
Its immutable SHA-256 is
`895a48bdc5d4e9cd101d916d80365102826279ef9490234de613b1434348f060`.
It includes the 233 previously protected artifacts, predecessor delivery records,
both lifecycle claims, actual acceptance evidence, final index and isolated
fictional acceptance sources. Local verification requires all files. Fresh CI
may skip only 27 explicitly listed ignored local files; tracked files always
remain mandatory, with the one documented historical Git representation allowed.
The earlier failed-run database remains absent.

No pre-existing tracked file changed. The baseline app/indexes, original corpus,
v2 labels/digest, historical Phase D claims/journals, model evidence and browser
captures remain intact. Projects 1–4 and the separate Production AI Knowledge Agent
were not accessed or changed.

## Limitations and next scope

This complete offline loop uses deterministic synthetic query vectors. A real
index can be inspected, but its passage search fails closed because no authorized
live query adapter exists. The finite grounding grammar can cause false
abstentions, and source-backed exactness is distinct from semantic truth.
Conservative topic/version ambiguity is unchanged. Snapshot checks detect observed
index changes; they are not a global lock beyond final validation. Deadlines are
cooperative, with SQLite progress/busy limits rather than a hard process kill.

The base environment stays unchanged. The optional SDK's zstandard wheel was
unavailable for local Python 3.15; a separate Python 3.13 environment was used.
Python 3.12/3.13 are the documented optional-stack targets.

A proposed small Milestone 4B is recorded in
[proposed-4b-budget.json](proposed-4b-budget.json): one invented two-document index,
two questions, at most **3 embedding POSTs** (one ingestion batch plus two query
embeddings), **6 metadata GETs**, **0 generation calls**, **600 seconds**, and zero
retries/downloads/substitutions/cloud calls. A separately approved exact plan and
offline-tested journaled single-use runner/query adapter are prerequisites. There
is no executable live flag or authorization implied by this proposal.

Work stops after offline delivery and exact-commit CI. No Milestone 4B or 5 work
is started.
