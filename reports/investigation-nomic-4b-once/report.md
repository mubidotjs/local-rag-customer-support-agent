# Milestone 4B: one real query acceptance

**PASS — the single authorized execution is complete and consumed.**

| Identity | Value |
| --- | --- |
| Prepared source | `eae02b56217e5231ece73b0c8641614875e4c1f3` |
| Exact execution commit | `31d995bbe97db9d6eab7d10770ca369deb7d97b2` |
| Delivery commit | Git commit containing this report; exact-SHA checks attached to that commit |
| Run ID | `investigation-nomic-4b-once` |
| Frozen plan SHA-256 | `d2f68baa13c4732a7d5a88c6fce2ffa04a6cbb4ea496ba08cc527a6c7882dec2` |
| Real model | `nomic-embed-text:latest` |
| Required and observed digest | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |
| Vector dimension | 768 |
| Runner wall-clock duration | 7.286576 seconds, including preflight; below 600 seconds |

## Final preflight

The Marin quote has **52 characters**. Independent extraction of the frozen source
confirmed normalized and source offsets **`[71,123)`**. The plan, source hash,
chunk/sentence mappings and runner assertions already agreed. Only the two prose
references to `[71,121)` were corrected before execution; no code, frozen labels,
plan hash or acceptance expectations changed.

At execution commit `31d995b`, a fresh full offline suite passed **387 tests** with
one real-inference skip. Its source/commit/log-bound proof is preserved in
[offline-proof.json](offline-proof.json) and [offline-tests.body](offline-tests.body).
The clean-tree readiness check verified all 309 protected files and absent live
paths. Both exact execution-SHA workflows passed **before** the live command:
[CI](https://github.com/mubidotjs/local-rag-customer-support-agent/actions/runs/44069366524),
[Offline investigation](https://github.com/mubidotjs/local-rag-customer-support-agent/actions/runs/44069366538).

## Actual requests and outcomes

| Operation | Actual embedding POSTs | Actual metadata GETs | Outcome |
| --- | --- | --- | --- |
| Initial two-document ingestion | 1 document batch | 2 | Two documents/chunks, 768D vectors, SQLite/FTS/source checks PASS |
| Supported Marin question | 1 query | 2 | Exact frozen sentence and citation; PASS |
| Unsupported Varos question | 1 query | 2 | Evidence-based abstention; PASS |
| **Total** | **3** | **6** | **PASS** |

Generation calls, retries, model downloads, substitutions, additional model
smokes/evaluations and cloud inference: **0**. Only the fixed runner was executed,
once. There were no model requests during offset correction, offline testing or
postflight verification. All nine request attempts were fsynced in the journal
before sending. Six tag responses verified the exact model identity before/after
each batch. All responses were HTTP 200 and passed validation.

| Case | Answer/abstention | Duration | Graph/tool use |
| --- | --- | --- | --- |
| Marin | `The marin access token remains valid for 18 minutes. [marin:c000]` | 2.209207 s | 12 steps, 3 tools, 1 query POST |
| Varos | `I cannot answer that from the available documentation.` | 0.427469 s | 12 steps, 3 tools, 1 query POST |

The supported citation is one complete exact sentence with normalized/source
offsets `[71,123)`, document `marin`, version `1`, and an explicit entity/facet
proof. The unrelated Tessel receipt sentence is not emitted.

Varos has no fact in either document. Its operational trace is successful through
all three tools and evidence assembly. Grounding rejects the Marin fact with
`entity_mismatch` and the receipt fact with `unsupported_relation`, leaving no
claim. The graph reports the existing coarse reason `invalid_selection`; this is
a deterministic evidence abstention, not a network/tool failure or generated
model output. That reason is preserved unchanged. More specific user-facing
abstention diagnostics remain a limitation.

## Real retrieval and provenance

| Question | Passage | Cosine | Semantic rank | FTS rank | RRF score |
| --- | --- | --- | --- | --- | --- |
| Marin | `marin:c000` | 0.9512856155147589 | 1 | 1 | 0.03278688524590164 |
| Marin | `tessel:c000` | 0.49744387635911835 | 2 | — | 0.016129032258064516 |
| Varos | `marin:c000` | 0.7577509715409486 | 1 | 1 | 0.03278688524590164 |
| Varos | `tessel:c000` | 0.4889256852347212 | 2 | — | 0.016129032258064516 |

Read-only postflight checks recomputed cosine, FTS ordering and equal-weight
RRF(k=60) directly from the saved real response vectors and designated index.
They matched the recorded results. The Varos near-match score did not bypass
entity grounding. Both source chunks fit this tiny corpus's retrieval window;
this is not a broad retrieval-recall benchmark.

Each investigation used `search_passages → inspect_versions → citation_metadata`
through the official local MCP SDK. Its 12 trace events have sequential steps,
consistent index stamps and matching canonical argument/result SHA-256 receipts.
Canonical retrieved sources, exact citation metadata and complete sentence offsets
match the read-only index. Both remain within the existing 20-step/4-tool limits;
5-second tool and 30-second graph sublimits were unchanged.

Full [supported response/trace](supported.json), [unsupported response/trace](unsupported.json),
[supported tool receipts](supported-tool-receipts.json),
[unsupported tool receipts](unsupported-tool-receipts.json),
[request journal](requests.jsonl), [raw request/response manifest](artifacts.sha256.json)
and [independent postflight checks](postflight.json) are preserved. Raw embedding
responses and identity checks are included as immutable `.body` artifacts.

## Index, claim and historical preservation

The designated fictional database is
`.local/investigation-nomic-4b-once/fictional.sqlite3`. Its initial, both post-query
and final SHA-256 is:

`871c74019540f913059d70d7534e42110671012bfd2739f22db89538d6ba17a2`

SQLite integrity/foreign keys, FTS5 correspondence, source hashes, complete quote
offsets, vector identity/configuration/dimension and stored vector hashes passed.
All journaled database opens target only this database. Every open during the two
question stages was read-only; `query_only=ON` was independently checked. No
historical database was opened as SQL during live execution. Historical hashes
were read as bytes.

All **309/309** protected artifacts remain unchanged, including the original 293,
old claims/indexes, immutable v2 labels, Phase D evidence and historical browser/
model captures. The frozen plan remains byte-identical. All 30 original runner
artifacts match the runner-created manifest, which has not been rewritten.

The consumed claim is retained at
`.local/investigation-nomic-4b-once.claim.json` and copied in
[run-claim.json](run-claim.json). Both have SHA-256:

`e82a991cfc892e93b2380fb6a2d9b7ac3500247d2a5cadb0f410be4d6d00a807`

The delivery changes only the offline regression's preparation-only absence
assertion into consumed-evidence validation/refusal when published output exists.
It still checks the same labels and budgets, verifies the immutable raw manifest,
and requires `unused_paths()` to reject reuse. No runtime, plan or acceptance
assertion changed after execution. Delivery CI runs offline and makes no model
requests. The execution proof/log are archived without being overwritten by
delivery tests.

## Scope and stop

This is an agent-curated, two-question fictional acceptance with genuine local
embeddings and deterministic planning/selection. It proves this bounded local
query/MCP/grounding path, exact citation support and safe missing-entity abstention.
It does not demonstrate generation quality, general semantic correctness,
independent human benchmark review or a broader adversarial score improvement.
Finite grammar, conservative conflict handling and snapshot limitations remain.

The single authorization is consumed. No additional live inference, rerun,
resumption, new run ID or Milestone 5 work is authorized or performed.
