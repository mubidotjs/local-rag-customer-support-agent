# Milestone 4B preparation report

Predecessor: `00261c7f421ad98ab980985c1e26ffb0beac7af4`.
Preparation SHA is the delivery commit containing this report; both GitHub
workflows publish exact-SHA checks on that commit.

**Offline preparation PASS. Live acceptance NOT AUTHORIZED / NOT EXECUTED.**
Actual model traffic in this task: **0 embedding POSTs, 0 query embeddings,
0 generation POSTs, 0 Ollama metadata GETs, 0 model downloads**. No new live claim
was consumed. Previous one-shot acceptances were not repeated.

## Delivery

- Opt-in asynchronous `ManagedQuery(ManagedOllama)` capability with exact pinned
  Nomic identity, compatible configuration, 768-dimensional vector validation,
  loopback-only cancellable requests and before/after identity checks.
- Existing cosine/FTS5/RRF ranking, ID tie-breaking, source offsets, read-only
  snapshots, receipts, quarantine and finite grounding rules retained. The tool
  revalidates the index stamp across the query await. No synthetic fallback.
- Fixed single-use runner with exclusive durable claim, exact payload/stage
  budget, durable pre-send journal, isolated SQLite fence, process deadline,
  partial evidence retention and independent outcome verification.
- Original FastAPI `/api/ask`, synthetic CLI, core dependencies and all original
  corpora/indexes/labels/evidence remain unchanged. No generation or LLM planning.

See [architecture, permission matrix and exact invocation](../../docs/investigation-live-preparation.md).
The optional dependency pins remain unchanged (LangGraph 1.2.22; MCP SDK 2.3.0).

## Frozen plan and independently checkable expectations

Fixed run: **`investigation-nomic-4b-once`**.
The [frozen plan](plan.json), including both LF sources and exact offsets, was
fixed before the new tests ran. SHA-256:
`d2f68baa13c4732a7d5a88c6fce2ffa04a6cbb4ea496ba08cc527a6c7882dec2`.

| Case | Frozen expectation |
| --- | --- |
| Marin token duration | Exact sentence “The marin access token remains valid for 18 minutes.” from `marin:c000`, source/normalized offsets `[71,123)` |
| Varos token duration | Abstention without citations: neither document mentions Varos. A tool failure does not satisfy the evidence-abstention expectation. |

The other document says each Tessel renewal creates 3 receipts. Both single chunks
fit in the retrieval window. These fictional labels are agent-authored, not an
independently human-reviewed benchmark. Passing the future test would establish
this bounded real-query/provenance path only; it would not demonstrate broad
semantic retrieval quality or answer-generation correctness.

Final preflight corrected only the earlier narrative offset typo `[71,121)`.
Independent extraction verifies the 52-character quote at `[71,123)` in both
source and normalized text. The frozen plan/digest, chunk mapping and runner
assertions already used those correct offsets and remain unchanged. The original
preparation results below describe the offline preparation, not the later live run.

## Offline verification

| Check | Result |
| --- | --- |
| Full optional-stack pytest | 387 passed, 1 real-inference skip |
| Base environment without optional SDKs | 283 passed, 36 optional/live skips |
| Vitest / production build | 4 passed / PASS |
| Synthetic Playwright | 4 passed, 2 real-model variants skipped |
| Query adapter statement / branch coverage | 73/73 (100%) / 26/28 (92.86%) |
| Runner statement / branch coverage | 318/336 (94.64%) / 107/130 (82.31%) |
| Combined investigation + runner coverage | 93.84%; CI gate at 90% combined |
| Historical preservation | 309/309 local files verified, including all original 293 |
| Actual fixed claim/workspace/database/output | All absent |

[coverage.json](coverage.json) contains file summaries and uncovered branches;
the full coverage JSON is also uploaded by the optional CI matrix. The broader
measurement includes the runner, unlike the narrower historical 4A measurement.
Uncovered CLI/defensive branches are visible; coverage is not complete proof of
security. [offline.json](offline.json) records exact source hashes and checks.

New tests block socket connections and use explicit fake transports. They cover
the full MCP/LangGraph supported/unsupported flow, payload parity with the unchanged
ManagedOllama query requests, no synthetic fallback, actual cosine/RRF parity,
source/receipt tampering, snapshot changes, instruction quarantine, dimensional and
metadata failures, strict read-only access, graph/request caps, cancellation and
timeouts. Separate runner tests mutate graph outcomes to prove a claimed success
cannot bypass frozen quote/offset/abstention checks.

A **mock-only rehearsal** completes exactly 3 embedding POSTs and 6 metadata GETs.
Failure rehearsals stop at 1, 2, 3 or 5 attempted requests, with durable pre-send
journals, retained consumed temporary claims and no silent retry. Published output
alone blocks reuse even if ignored local run files are removed in a test clone.
These temporary fake claims are not the designated live claim.

## Preservation and readiness

The [new 309-file inventory](preservation.json) adds immutable 4A evidence and
unchanged source/docs/tests to the original historical 293. Its SHA-256:
`f33c8f5df9d1bb763156f5e09e0cfc2091a3c119f0aeb3842c2d9ab7c433aab0`.
All original labels/hashes, Phase D journals/reports, model/browser evidence,
original indexes and consumed claims remain unchanged. Only the three authorized
4A implementation modules and optional coverage CI command were modified; all
remaining delivery files are new. Fresh CI may skip only the 27 explicitly listed
ignored historical files. Live preflight requires every local historical file.

The fixed live paths remain absent:

```text
.local/investigation-nomic-4b-once
.local/investigation-nomic-4b-once/fictional.sqlite3
.local/investigation-nomic-4b-once.claim.json
reports/investigation-nomic-4b-once
```

Readiness additionally requires a clean exact source commit and a fresh successful
offline test proof bound to that commit, source files and log hash. Readiness
checks create none of the live paths and contact no model. Current installation
identity has not been rechecked against Ollama; that belongs inside the separately
authorized request budget. A future model mismatch fails closed.

Requested ceiling: **3 embedding POSTs** (1 document batch + 2 queries), **6 model
metadata GETs**, **0 generation**, **600 seconds overall**, zero retries,
downloads, substitutions or cloud calls. The fixed graph retains its finite
20-step/4-tool caps, 5-second tools and 30-second investigations. CPU inference
that exceeds those sublimits stops; it receives no automatic timeout increase.

After separate explicit owner authorization of the exact preparation SHA, frozen
plan, fixed ID and ceiling, the future invocation from the repository root is:

```powershell
.\.local\investigation-venv\Scripts\python.exe -m evaluation.investigation_live --execute-authorized-once
```

**That command was not executed against the real repository in this task.** Only
fake-transport tests ran in temporary roots. A crash/hard timeout can leave an
`INCOMPLETE_NOT_PASS` checkpoint rather than a final report; its claim and request
journal remain consumed and cannot be resumed. Loopback tag checks assume an
honest local daemon and owner-controlled model registry, as before.

No live acceptance, model download, deployment or Milestone 5 work is included.
