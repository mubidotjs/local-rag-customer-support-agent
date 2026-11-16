# Single real lifecycle acceptance — stopped before ingestion

**Outcome: STOPPED; live lifecycle acceptance was not established.** The single authorized execution is consumed. It was not retried, resumed or replaced with synthetic acceptance.

| Provenance | Value |
| --- | --- |
| Approved application source | `9c3bdb0879b247969a9bc67cda044ad095b97a07` |
| Executed runner commit | `c38b8c1f1119f75e0799b36458f28e5c833b2035` |
| Run | `lifecycle-real-once` |
| Started (UTC) | 2026-11-16T15:52:22.874288+00:00 |
| Elapsed including preflight | 2.584278 seconds |
| Intended model | `nomic-embed-text:latest` |
| Required digest | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |
| Observed model identity | **UNVERIFIED — no metadata response received** |
| Delivery commit and exact-SHA CI | Published with the delivery response; this report is part of that commit |

## Counts and failure

One metadata GET attempt was durably journaled before transport. The fixed local endpoint `http://127.0.0.1:11434/api/tags` could not be connected to. The recorded transport exception is **`httpx.ConnectError: All connection attempts failed`**, surfaced by the production adapter as `ModelError: Pinned model metadata verification failed`. No HTTP status or response body was received. This establishes a local connection failure, not a model identity mismatch or embedding-quality failure.

| Request type | Attempted | Successful |
| --- | ---: | ---: |
| Metadata GET | 1 | 0 |
| Document embedding POST | 0 | 0 |
| Query embedding | 0 | 0 |
| Generation POST | 0 | 0 |
| Download / substitution / retry / smoke call | 0 | 0 |

The attempt is charged against the authorized metadata budget despite the failed connection. There were no extra health checks, model inspection requests, service restarts or follow-up model calls. No second live batch was executed.

## Operation and index status

| Authorized stage | Live result | Vector reuse / regeneration |
| --- | --- | --- |
| Initial ingestion (two invented single-chunk sources) | Stopped at the first identity GET, before embedding or storage | Not reached |
| Add | NOT RUN | Not measured |
| Content update | NOT RUN | Not measured |
| Metadata-only update | NOT RUN | Not measured |
| Compatible no-op reindex | NOT RUN | Not measured |
| Explicit delete | NOT RUN | Not measured |

The explicitly designated `.local/lifecycle-real-once/fictional.sqlite3` **does not exist**. Consequently there is no new index hash, embedding dimension, committed vector, retrieval candidate or live deletion behavior to report. The two fictional source files were created only in the fresh workspace and copied into the execution artifacts. There were no SQLite opens during this failed run. All source-offset, vector-reuse, atomicity, stale-preview and deletion guarantees remain supported by the prior offline tests; they were not newly demonstrated with real embeddings.

## Preflight, preservation and evidence

Before the live attempt: approved source ancestry and unchanged application files were verified; **210 offline pytest tests passed**, with the real-model test and not-yet-published evidence check skipped. **4 Vitest tests**, the production build and **4 synthetic desktop/mobile Playwright tests** passed; real browser variants were skipped. Budget/failure/isolation tests used fake transports, including a full synthetic six-stage run, metadata/embedding failures, identity changes, exhausted/forbidden budgets, deadlines, journal failure and single-use claim enforcement. These synthetic successes are not substituted for live acceptance.

The source/test/log proof is pinned to the executed runner commit in [preflight.json](preflight.json). The pre-send [journal](requests.jsonl), [request](requests/001.json), immutable [execution report](report.json), checkpoint, source copies and [consumed claim copy](run-claim.json) are preserved. The original claim remains at `.local/lifecycle-real-once.claim.json` and matches the published copy byte-for-byte. The historical Phase D claim remains unchanged.

All **209 pre-existing protected artifacts** match their preflight hashes after the stopped run. This includes all 139 historical manifest entries, the seven prior relevance artifacts, the 12 original indexes, frozen golden/v2/adversarial labels, corpus, previous evaluation reports, model/browser evidence and all baseline tracked files. [publication-audit.json](publication-audit.json) records the offline after-run check. No historical database was opened through SQLite; preservation used byte hashing only.

Execution manifest SHA-256: `bdc5327b1410c1d8e4e51b0a8308606325564bf2da73ed41b642cca079846b9b`. The [execution manifest](artifacts.sha256.json) was written when the run stopped and remains unchanged. Later publication files have a separate manifest. Recorded-evidence CI checks validate the actual stopped result without contacting Ollama.

## Limits and next action

The connection failure prevented real acceptance. The model installation/digest was not verified, and there is no evidence of newly generated embeddings or successful live document mutation. No application change was made in response to the failure. No service was started and no request was retried. Both the historical Phase D claim and this new lifecycle claim remain consumed and intact. Any future real attempt requires separate explicit owner authorization and a new, separately identified plan; none is requested or executed here.
