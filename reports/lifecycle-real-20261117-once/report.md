# Milestone 3 — single real lifecycle acceptance PASS

**PASS.** The owner-authorized frozen six-stage acceptance executed once using actual local `nomic-embed-text:latest` embeddings. The scoped Milestone 3 lifecycle acceptance is complete. No Milestone 4/5 work, generation, query embedding, browser acceptance, additional benchmark or retry was performed.

| Provenance | Value |
| --- | --- |
| Exact authorized/executed preparation commit | `1518d774e65dfc9c34739b5778546f10a0f46b0b` |
| Unchanged application/lifecycle source | `9c3bdb0879b247969a9bc67cda044ad095b97a07` |
| Hash-pinned original runner | `c38b8c1f1119f75e0799b36458f28e5c833b2035`; source SHA-256 `1f3a68e97dc784d2a5211657e002acb72bd62c8524b5c1a4801f601ba897dcdc` |
| Run ID | `lifecycle-real-20261117-once` |
| Recorded core acceptance elapsed | 4.034733 seconds, including its preflight; below the 600-second overall deadline |
| Recorded start/end (UTC) | 2026-11-17T08:12:38.744254+00:00 / 2026-11-17T08:12:42.445244+00:00 |
| Fresh postflight completed (UTC) | 2026-11-17T08:12:42.687175+00:00 |
| Delivery SHA and exact-SHA CI | Published with the delivery response; this report is included in that commit |

## Actual model identity and requests

All six budgeted GET `/api/tags` responses verified the same local model identity: **`nomic-embed-text:latest`**, digest **`0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`**. Every embedding response named that model and contained **768-dimensional finite nonzero vectors**. No additional version/capability/smoke check was made. The generation model was not invoked or independently inspected.

Exactly **3 embedding POSTs and 6 metadata GETs**, all HTTP 200, in GET/POST/GET order for each of the first three operations. **Zero query embeddings, generation POSTs, downloads, cloud inference, substitutions or automatic retries**. Each attempt was reserved and fsynced to the [journal](requests.jsonl) before transport. Nine attempts and nine verified completions are preserved with request/response bodies and hashes. Initial ingestion generated two vectors in one batch; add and content update generated one vector each in one batch.

## Live operation results

Counts include retained chunks across the entire fictional index. This table records actual local embeddings, not synthetic test output.

| Operation | Result | Vectors reused | New vectors | Embedding POSTs | Metadata GETs |
| --- | --- | ---: | ---: | ---: | ---: |
| Initial ingestion: two fictional single-chunk sources | PASS | 0 | 2 | 1 | 2 |
| Add one fictional document | PASS | 2 | 1 | 1 | 2 |
| Update its content | PASS | 2 | 1 | 1 | 2 |
| Metadata-only update | PASS | 3 | 0 | 0 | 0 |
| Compatible no-op reindex | PASS | 3 | 0 | 0 | 0 |
| Explicit deletion | PASS | 2 | 0 | 0 | 0 |

The Selene and Torin chunk records, vector JSON hashes, IDs and source offsets are identical across all six snapshots. Wren's content update produced a different vector hash; the metadata-only update retained that exact vector hash while correctly shifting raw offsets from `[68,119)` to `[87,138)`. Metadata and no-op snapshots, including database hashes, are identical. The removed Wren document is absent from committed SQL, FTS matches and the full validated semantic candidate list. No query embedding or answer generation was used to establish deletion.

After each operation the runner verified committed document/source membership, exact normalized/raw source offsets, embedding model/configuration/dimensions, finite vectors, SQLite integrity, foreign keys, FTS content and inverted-index integrity. FTS verification writes were rolled back, with unchanged file hashes checked. A stale preview was deliberately rejected without an adapter and with **zero requests/writes**. The 38 recorded SQLite opens all targeted only `.local/lifecycle-real-20261117-once/fictional.sqlite3`.

| Committed stage | SQLite SHA-256 |
| --- | --- |
| Initial | `2e64c27638e073523c48d4aad075a13420436d59bee0a81a6dc476361463a0b0` |
| Add | `374e21542e5e7a737882bcf5f80fd835b261bfc6377abdc8ed689c6e872dfe5f` |
| Content update | `a24377bf579788578b60e3dbef840e323c8314cb27f46e2106b82344552f0a46` |
| Metadata update | `e9ea2449b9e69152ef3d602fcffacb5be045f96d721a522d567c6f877ec6805c` |
| No-op reindex | `e9ea2449b9e69152ef3d602fcffacb5be045f96d721a522d567c6f877ec6805c` |
| Delete/final | `081adff9cb8c20f5584f5fdad7744005e171e2a22f8e3148639771642962fa8b` |

The final fictional database remains in the ignored isolated workspace, and its bytes match the recorded final hash. It is not substituted for any original index.

## Preservation and immutable evidence

All **233 protected historical files** match the frozen preservation snapshot, before, during and after execution. This includes the original 139 historical entries, 12 original indexes, all prior model/browser evidence, corpora, golden/v2/adversarial labels, the consumed Phase D claim and the earlier failed lifecycle claim/report/journal. The earlier STOPPED result remains unchanged. The preparation plan, preservation snapshot, offline report, guide and runner source also remain unchanged. Historical databases were read only as bytes for preservation; they were never opened through SQLite.

The new claim is consumed and retained at `.local/lifecycle-real-20261117-once.claim.json`, matching its [published copy](run-claim.json) exactly. [fresh-postflight.json](fresh-postflight.json) records PASS and retained predecessor claims. No rerun, claim removal, resume or fallback ID occurred.

The raw [execution report](report.json), checkpoint, [preflight](preflight.json), [previews](previews/), [snapshots](snapshots/), [source copies](sources/), requests and response bodies remain unchanged. Execution manifest SHA-256: **`a57210f0b623e259e53abc1e0ad583c6bff5006ea571d8de40f32b5bc048f359`**; all **40** entries verify. Publication adds this human-readable report, an offline audit and a separate publication manifest without changing the execution manifest or recorded outcomes.

Recorded-evidence tests verify the actual request counts/identities, immutable hashes, vector reuse/regeneration, exact source offsets, no-op equality and deletion. The preparation-state test now recognizes a published consumed run and requires reuse to be rejected, including in CI without ignored local files. This is a test-only post-execution state adjustment; application code, both runner sources, frozen plan, labels and historical results are not changed. Exact-SHA CI remains completely offline and does not execute the live command.

## Scope and limitations

This establishes the bounded real embedding/storage lifecycle acceptance on a small fictional corpus. It does not establish new generation quality, query/retrieval quality, public deployment or production-data acceptance. Concurrent-reader, crash and rollback guarantees remain covered by offline tests; no additional live fault-injection experiment was authorized or run. Source trust and local daemon identity limitations remain as previously documented. No failure or budget overrun occurred in this run. All model access stopped after the six stages; only offline evidence review/publication followed.

Milestone 3's scoped acceptance is PASS. Work stops here; no Milestone 4/5 scope or additional inference budget is inferred.
