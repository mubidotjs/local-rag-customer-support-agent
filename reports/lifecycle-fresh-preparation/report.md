# Fresh lifecycle acceptance — preparation only

**Status: offline preparation passes; awaiting separate explicit live authorization.** Actual embedding, metadata, query, generation, download and cloud requests during this task: **zero**. The designated new live claim, workspace, SQLite database and output do not exist. No successful real embedding/lifecycle acceptance is claimed.

## Reviewed state and immutable predecessor

- Repository started clean at `a90b553394a5d5312734eac16346c1ac603a224b`.
- Application/lifecycle source remains `9c3bdb0879b247969a9bc67cda044ad095b97a07`.
- Original runner commit: `c38b8c1f1119f75e0799b36458f28e5c833b2035`; runner source SHA-256 remains `1f3a68e97dc784d2a5211657e002acb72bd62c8524b5c1a4801f601ba897dcdc`.
- Previous `lifecycle-real-once` claim is consumed. Its unchanged report/journal record STOPPED, one failed metadata GET, zero embedding POSTs and zero generation calls. The historical Phase D claim remains consumed and unchanged.
- The new wrapper imports the pinned runner in an isolated module namespace. Neither the old runner file nor its global run ID is modified. Application code, corpora, model identity, frozen labels, old results and indexes remain unchanged.

## Fixed new proposal

Run ID: **`lifecycle-real-20261117-once`**. Database: `.local/lifecycle-real-20261117-once/fictional.sqlite3`. Claim: `.local/lifecycle-real-20261117-once.claim.json`. Journal/output: `reports/lifecycle-real-20261117-once/requests.jsonl`. All these live paths are unused.

[Fixed plan](plan.json), SHA-256: `0aeca1c4ac2aabebf2f5d2d64ca8a4ed34661a446e61260b3bf8ebf54a17c5d3`.

Ceilings remain **3 embedding POSTs, 6 metadata GETs, zero query/generation/download/cloud/substitution calls, 600 seconds overall, one attempt per operation, no automatic retry**. Model: `nomic-embed-text:latest`; required digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`, dimension 768. The owner-reported Ollama version, model registration/blob/capability checks and CPU/memory conditions are not independently rechecked here. The model store in the other project is neither read nor modified.

The inherited initial/add/update operations have one batch each. Metadata-only update, no-op reindex and delete permit zero requests. Pre-send durable counters/journal, exact tag checks before/after each embedding batch, bounded time/response sizes, no redirects/proxies/retries, exclusive persistent claim creation, checkpointing and the single SQLite target are retained. An outer deadline includes preflight and fresh postflight verification. Claims or paths cannot be selected dynamically or replaced to continue.

## Offline verification

Local results: **235 pytest passed, 1 real-model test skipped; 4 Vitest passed; TypeScript/Vite build passed; 4 synthetic desktop/mobile Playwright passed, 2 real browser variants skipped**. Exact-SHA CI and the preparation commit accompany the delivery response.

Twenty-two fresh preparation tests supplement the unchanged original runner budget/failure tests and all existing lifecycle/grounding/security tests. Fake HTTP tests exercise the complete six-stage flow through the unchanged production adapter/storage, with temporary fictional databases and temporary fake claims only. Simulated counts are 3 embedding POSTs and 6 metadata GETs; real counts are zero. Vector reuse counts are `[0,2,2,3,3,2]` and new-vector counts `[2,1,1,0,0,0]`. The three zero-call stages remain zero. Source offsets, committed SQL/FTS membership, dimension, identity/configuration, vector-byte preservation and stale-preview rejection are verified by the reused runner. Existing rollback, crash and concurrent-read tests pass offline.

Focused checks cover predecessor claim edits/removal, journal/label edits, fixed-ID retargeting, wrong execution provenance, fresh path reuse, an exclusive-claim creation race, failure before/after embedding, no transport without an explicit fake in tests, rejected arbitrary ID/path options, stale test proofs, source changes and read-only preparation checks in a clean checkout. The old claim cannot be used by the new ID or reopened through the historical runner; it is never deleted/recreated. Failed fake attempts retain only their temporary new claims. No real run claim is consumed by testing.

## Preservation proof

[Snapshot](preservation.json), SHA-256: `9a37569d38fdb00ebdfdfd468dc5f4c50422ddc39701e5677987775ef267817e`. **All 233 files verify locally**: the original 139 historical entries, all baseline tracked files, consumed claims, previous stopped-run records, both previous local proof/log files and the failed workspace's two source files. The 12 original SQLite indexes, golden/v2/adversarial labels, model/browser evidence and prior reports remain unchanged. The predecessor's failed database remains absent. Hashing is byte-only; historical databases are not opened through SQLite.

Fresh CI verifies all available files, permitting absence only for the 20 explicitly enumerated ignored local artifacts. Known Git LF representation is accepted only for the exact original baseline report alternative already documented; arbitrary edits are rejected. [offline.json](offline.json) records new-code hashes, zero real requests and unused live paths. The snapshot and plan were pinned before the new tests were executed.

## Readiness and approval boundary

The implementation is ready for the separate authorization step after exact-commit offline proof and the read-only `--check-preparation` gate pass. A successful check creates no live files and makes no model calls. Runtime network availability, CPU cold-load latency and successful live mutations remain unverified in this preparation. The inherited per-request limit is 90 seconds; neither warm-up nor automatic tuning/retry is allowed. Any failure consumes the new future claim and stops without fallback.

Proposed exact live invocation, from the project environment:

```text
python -m evaluation.lifecycle_live_fresh --execute-authorized-once
```

This was **not executed**. The owner must separately approve the delivered preparation SHA, fixed run ID, pinned digest, three-POST/six-GET ceiling, zero query/generation/download/cloud/substitution requests, ten-minute cap and one-attempt/no-retry rule. Full invocation, local proof/readiness workflow and suggested approval wording: [preparation guide](../../docs/lifecycle-fresh-preparation.md).
