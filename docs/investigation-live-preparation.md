# Milestone 4B: query adapter and offline acceptance preparation

**Preparation only. No live execution is authorized by this document or CLI flag.**
The approved predecessor is `00261c7f421ad98ab980985c1e26ffb0beac7af4`.
Historical 4A architecture/results remain unchanged and describe their original
scope. This document describes the new opt-in query capability.

## Architecture and permissions

`ManagedQuery` subclasses the existing `ManagedOllama`. Construction makes no
requests. It reuses `Binding`, `config_digest` and `unit`, with the same exact
Nomic name/digest, `search_query: ` prefix, `truncate: false`, `num_ctx: 2048` and
`keep_alive: 5m`. The existing ManagedOllama ingestion path continues to send
`search_document: ` inputs. Offline tests compare the actual query payload with
that unchanged production adapter's payload.

The async query method uses cancellable HTTPX I/O, not a worker thread around
`asyncio.run`. There are no background queries continuing after cancellation.
Requests bind to `http://127.0.0.1:11434`, disable environment proxies and redirects,
use no retry transport, and verify `/api/tags` immediately before and after the
embedding POST. Remote model fields, duplicate identities, wrong digests/names,
malformed responses and invalid vectors are rejected. A vector must be finite,
nonzero and exactly 768-dimensional. A failed or cancelled capability is sealed;
the default permits one query, and the fixed runner explicitly permits two.
Inherited synchronous embedding and generation methods are explicitly disabled
on this query-only capability; document ingestion uses ManagedOllama separately.

Trusted startup code must explicitly inject it:

```python
# Construction only; invoking investigation would make model requests and needs
# separate authorization. This is not an instruction to execute live now.
adapter = ManagedQuery(settings)
tools = ReadOnlyTools(settings, query_adapter=adapter)
```

Tool arguments cannot select an adapter, URL, database, model or limits. There is
no general-purpose `--live` investigation flag. The old CLI, synthetic execution
and `/api/ask` remain independent and backward-compatible. The optional dependency
pins are unchanged: LangGraph 1.2.22 and official MCP SDK 2.3.0.

| Capability | Authority and allowed I/O |
| --- | --- |
| Default investigation | Read-only metadata; real passage search refused |
| Explicit synthetic investigation | Existing digest/config-bound eight-dimensional fake query vectors; no model requests |
| Explicit ManagedQuery investigation | Read-only selected SQLite index; only fixed-loopback Nomic tags/embed requests |
| Five existing MCP tools | Same strict schemas, budgets, receipts, source validation and no mutation/SQL/path/network arguments |
| Fixed acceptance runner | One fresh fictional index write during initial ingestion only; all subsequent SQLite opens read-only |
| Generation, downloads, cloud, external servers, mutation tools | Not implemented or allowed |

Before query I/O, `search_passages` verifies the complete index and matching model,
configuration and dimension. It releases its read snapshot across the await,
reopens a verified snapshot, and requires the pinned logical stamp to match before
ranking. It rechecks the binding, applies the unchanged cosine/FTS5/RRF algorithm,
and issues an out-of-band receipt. Existing evidence reconciliation and exact
sentence/entity/facet validation still run. Nothing falls back to synthetic vectors
or rewrites unsupported claims. Diagnostics now report attempted query POSTs per
investigation and distinguish `local-managed-query` from offline modes.

## Frozen acceptance design

Run ID: **`investigation-nomic-4b-once`**.

| Purpose | Fixed repository-relative path |
| --- | --- |
| Workspace | `.local/investigation-nomic-4b-once` |
| Database | `.local/investigation-nomic-4b-once/fictional.sqlite3` |
| Exclusive claim | `.local/investigation-nomic-4b-once.claim.json` |
| Future immutable evidence | `reports/investigation-nomic-4b-once` |
| Offline proof, not a claim | `.local/investigation-4b-offline-proof.json` |

[plan.json](../reports/investigation-live-preparation/plan.json) was frozen before
new tests ran. SHA-256:
`d2f68baa13c4732a7d5a88c6fce2ffa04a6cbb4ea496ba08cc527a6c7882dec2`.
It contains the exact LF sources, source hashes, sentence/chunk IDs, offsets,
questions and expected outcomes. Labels are agent-authored fictional acceptance
criteria, not independently human-validated benchmark labels.

| Stage | Evidence or expected outcome | Maximum requests |
| --- | --- | --- |
| Ingest two single-chunk documents | Marin token: 18 minutes; Tessel renewal: 3 receipts | 1 embedding POST + 2 metadata GETs |
| Supported question | “How long does the marin access token remain valid?” Exact quote “The marin access token remains valid for 18 minutes.” from `marin:c000`, source/normalized offsets `[71,123)` | 1 query embedding POST + 2 metadata GETs |
| Unsupported question | “How long does the varos access token remain valid?” Abstain, no citations; Varos is absent from both documents | 1 query embedding POST + 2 metadata GETs |

The supported question must produce exactly the specified sentence and citation
offsets. The unsupported question must abstain for a documented evidence reason;
a tool failure or timeout does not count as a successful unsupported acceptance.
The two-document corpus ensures both chunks fit the retrieval window, making the
outcomes independently checkable. This tests actual embedding plumbing, score
computation and exact grounded selection; it does not establish broad semantic
retrieval quality or generation capability. There is no generation call.

Preflight prose correction: the earlier `[71,121)` description was a typo. The
52-character Marin quote, frozen plan, extracted chunk/sentence mappings and
runner assertions consistently use `[71,123)`. No frozen expectation or code
changed for this correction.

Proposed total ceiling: **3 embedding POSTs** (one document batch and two queries),
**6 metadata GETs**, **0 generation calls**, **600 seconds overall**, one attempt
per operation, zero automatic retries/downloads/substitutions/cloud requests.
Existing graph caps remain 20 steps and 4 tool attempts per question; the fixed
runner selects 5 seconds per tool and 30 seconds per graph, within the existing
hard schema bounds. A slow CPU query can therefore stop before the overall cap;
it will not be retried or given a larger timeout automatically.

## Single-use enforcement and evidence

The runner verifies a clean Git tree, approved predecessor ancestry, a finite
delivery-file allowlist, local SDK versions, immutable plan/preservation digests,
all historical files and a successful offline proof bound to the current commit,
source hashes and test-log hash. Readiness inspection creates no live paths.

Execution exclusively creates and fsyncs the fixed claim before any model request.
Any existing claim, workspace, database or published output refuses execution,
including a clone where only published evidence remains. There is no run-ID/path
override, resume, fallback or rerun option. Old claims are never rewritten.

A request budget allows only the exact three stages and `GET tags → POST embed →
GET tags` order for each. It compares complete payloads with the frozen document
texts/questions, prefixes and configuration. It rejects credentials, other origins,
endpoints, payloads, counts or repeats. Every permitted attempt, including a failed
send, reserves budget and fsyncs a pre-send journal event before the transport is
called. A failure seals the budget. Raw bounded responses, identities and latencies
are preserved, without automatic retries.

A SQLite connection fence permits only the fixed fictional database. Writes are
allowed only during initial ingestion; later connections require an exact
`mode=ro` URI. Normal read connections additionally enforce `query_only=ON` and
`trusted_schema=OFF`. Historical databases are hashed as bytes for preservation,
not opened through SQLite. The fence covers this runner's known application code;
it is not an operating-system sandbox for arbitrary malicious Python.

After ingestion and both investigations, the runner verifies SQLite integrity,
foreign keys, FTS5/source correspondence, model configuration/dimension and exact
sentence offsets. Index and vector hashes must remain unchanged across querying.
It saves full responses/traces and issued tool results, including actual cosine,
FTS/RRF ranks and scores. It independently checks fixed outcomes rather than
trusting a graph's claimed success. Failure retains the claim, partial evidence,
counts and STOPPED report. An OS/process interruption leaves an
`INCOMPLETE_NOT_PASS` checkpoint and pre-send journal; absence of a final PASS is
never success. A hard timer exits the live process at the 600-second ceiling,
including preflight; it does not restart or resume it.

## Offline verification and preservation

All new tests use fake transports and temporary invented indexes; new suites block
socket connections. They exercise payload parity, both expected outcomes through
the actual MCP/graph chain, no synthetic fallback, source quarantine, receipts,
changed snapshots, malformed metadata/vectors, timeouts/cancellation, request
ordering and ceilings, partial-failure persistence, independent outcome checking,
read-only access and unavailable optional SDKs.

The original 293-file inventory is unchanged. The separate
[preservation inventory](../reports/investigation-live-preparation/preservation.json)
protects **309** files, adding 4A reports, traces, coverage, dependency pins and
unchanged source/tests/docs. The three adapted 4A modules and optional CI coverage
command are authorized implementation changes, not historical evidence rewrites.
Snapshot SHA-256:
`f33c8f5df9d1bb763156f5e09e0cfc2091a3c119f0aeb3842c2d9ab7c433aab0`.
Local verification requires all 309 files, including every consumed claim. Only
the 27 explicitly enumerated ignored files may be absent in fresh offline CI.

## Future invocation — not authorized now

From the repository root, using its optional SDK environment, the exact future
command requiring separate owner authorization of the preparation commit,
frozen plan, fixed ID and requested budget is:

```powershell
.\.local\investigation-venv\Scripts\python.exe -m evaluation.investigation_live --execute-authorized-once
```

The flag is an execution guard, not proof of approval. **Do not run it during
preparation.** The safe readiness command is:

```powershell
.\.local\investigation-venv\Scripts\python.exe -m evaluation.investigation_live --check-preparation
```

If code/commit changes, the ignored offline proof must be regenerated after a
successful full offline suite at that commit. The helper
`record_offline_proof(0)` accepts only a successful UTF-8 log at
`.local/investigation-4b-offline-tests.log` and binds it to source hashes/current
Git HEAD. It does not create a live claim. Missing historical local files or stale
proofs stop readiness; there is no bypass switch.

No new real model acceptance is claimed here. No Milestone 5 work is included.
