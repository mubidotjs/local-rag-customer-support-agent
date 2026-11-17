# Fresh bounded lifecycle acceptance — offline preparation

The owner authorized preparation and offline testing only on 2026-11-17. **No real model request or new live claim is authorized by this preparation.** The owner separately reported Ollama 0.40.16, working version/tags endpoints, the exact Nomic digest/capability/dimension, complete model blobs and CPU-only memory conditions. These observations are attributed to the owner; this task neither rechecks them over HTTP nor accesses the model store in another project.

## Distinct fixed run

| Item | Proposed value |
| --- | --- |
| Run ID | `lifecycle-real-20261117-once` |
| Workspace | `.local/lifecycle-real-20261117-once` |
| Sole SQLite target | `.local/lifecycle-real-20261117-once/fictional.sqlite3` |
| Persistent claim | `.local/lifecycle-real-20261117-once.claim.json` |
| Pre-send journal | `reports/lifecycle-real-20261117-once/requests.jsonl` |
| Evidence output | `reports/lifecycle-real-20261117-once` |
| Previous consumed ID | `lifecycle-real-once` — remains unchanged and unavailable for reuse |

No new live paths are created during this task. Offline tests create fake claims and fictional databases only inside pytest temporary directories. The fresh entry point has no arbitrary run-ID, database-path, resume or automatic-fallback options.

The original `evaluation/lifecycle_live.py` file and historical module globals remain unchanged. The entry point loads that exact hash-pinned, trusted runner source in a private module namespace and assigns only the fixed new run ID/root there. It reuses the original budget, HTTP transport instrumentation, SQLite path restriction, six-operation sequence, previews/confirmation, exact vector-hash and offset checks, FTS/SQLite integrity checks, checkpoint/report writing and atomic exclusive claim creation. It does not import or execute document text as code.

Before and after future execution, the fresh entry point verifies all available historical files against a frozen 233-file snapshot. Real execution requires **all** local claims/indexes/evidence, including the original ignored artifacts. CI skips only the 20 explicitly listed ignored local files absent from a clean checkout. The old failed report must remain STOPPED with one metadata attempt and zero embedding POSTs; its original claim must match its published copy. The old failed database must remain absent. Altering/removing historical claims or labels fails before a fresh claim is created.

The inherited safeguards keep the new claim consumed even on failure. An existing fresh claim, workspace or output blocks execution; no path is deleted or renamed to continue. A new entry point does not unblock the historical runner. A missing/altered fresh claim after execution is also an error. Partial results are retained, and no automatic second attempt exists.

## Proposed ceiling and sequence

Only `nomic-embed-text:latest`, digest `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`, with 768-dimensional finite nonzero vectors. The generation model is unused and not inspected.

Maximum **3 document embedding POSTs**, **6 metadata GETs**, **0 query embeddings**, **0 generation POSTs**, **600 seconds overall**, one attempt per operation, zero retries/downloads/cloud calls/substitutions. Each of the first three operations permits only GET `/api/tags`, POST `/api/embed`, GET `/api/tags` in that order. All failed attempts consume counters before transport. No extra `/api/version`, warm-up, smoke call or model-download request is allowed. CPU-only cold loading still has the inherited 90-second request deadline; a timeout stops the run without tuning or retry.

1. Initial ingestion: two invented single-chunk documents; one embedding batch for two vectors.
2. Add: one invented single-chunk document; one new vector, two reused vectors.
3. Content update: one new vector, two unchanged vectors retained exactly.
4. Metadata-only update: all three vectors reused; zero requests.
5. Compatible no-op reindex: all three vectors reused; zero requests and unchanged database hash.
6. Explicit delete: two retained vectors; zero requests; removed document absent from SQL, FTS and validated semantic candidates.

Committed sources, exact offsets, model/configuration identity, dimensions, vector JSON hashes and database hashes are checked after every operation. A deliberately stale preview must reject with zero requests/writes. Rollback/concurrency/crash atomicity tests remain offline; the future sequence checks committed consistency without adding extra model operations or query embeddings. The journal is fsynced before each transport attempt, and failed attempts are counted. Identity mismatch, failed response, uncertain/exceeded budget, unexpected database access, integrity/preservation failure or timeout stops execution. The outer deadline also covers preflight and the fresh postflight preservation check.

## Readiness and future invocation

From the repository root using the project virtual environment, this is the **read-only** readiness command:

```powershell
python -m evaluation.lifecycle_live_fresh --check-preparation
```

It creates no live directory, claim, timer thread or model connection. It requires a clean tree, ancestry from `a90b553394a5d5312734eac16346c1ac603a224b`, only the listed preparation additions, the unchanged original runner, all historical artifacts, unused fresh paths, and successful offline proof on the exact current commit. The proof is separate from both historical proofs and all run claims:

- `.local/lifecycle-fresh-offline-tests.log`
- `.local/lifecycle-fresh-offline-proof.json`

After running `python -m pytest -q` on the exact preparation commit and saving its output to that new log, `evaluation.lifecycle_live_fresh.record_offline_proof(0)` records the successful test exit code, commit, code/test/plan/snapshot hashes and log hash. New code or a new commit invalidates the proof until tests are run again. A successful readiness result does not grant authorization. Default execution and unknown ID/path options are rejected.

The proposed future live command is exactly:

```powershell
python -m evaluation.lifecycle_live_fresh --execute-authorized-once
```

**Do not execute that command during preparation.** It may run once only after separate explicit owner approval of the final preparation commit, the fixed new run ID and paths, pinned model/digest, six-stage sequence and all ceilings. The CLI flag is an execution guard; it does not itself establish that the owner authorized the task.

Suggested owner authorization wording (replace `PREPARATION_SHA` with the delivered exact SHA):

> I authorize ONE execution of `python -m evaluation.lifecycle_live_fresh --execute-authorized-once` at `PREPARATION_SHA` for `lifecycle-real-20261117-once`, using only the pinned Nomic digest, a new fictional database, at most 3 embedding POSTs and 6 metadata GETs, zero query/generation/download/cloud/substitution calls, a 600-second overall cap, one attempt per operation and zero automatic retries. Preserve both previous consumed claims and all historical evidence; stop and preserve partial results on any failure. No additional model checks or experiments are authorized.

No LangGraph, MCP, document architecture changes, deployment, scheduling or other project work is part of this preparation.
