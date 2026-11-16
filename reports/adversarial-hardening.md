# Independent review and offline hardening — 2026-11-16

**Phase D: NOT RUN. Zero new real embedding or generation requests. No case is human-approved.** This milestone changes offline contracts and prepares review packages; baseline model acceptance remains historical evidence for the baseline commit, not a new live acceptance claim.

## Preservation

The original 13 corpus files, `evaluation/golden.json`, `reports/local-evaluation.json`, and `reports/verification.md` are unchanged. Their hashes are pinned in `evaluation/baseline-manifest.json`, separately recording original local bytes and original baseline Git blob bytes where line endings differ. Only those exact representations are accepted; unrelated edits still fail. The existing local `data/support.sqlite3` and both original model-backed browser screenshots are also fingerprinted and preserved. Local Playwright used a separate output directory. A read-only structural audit of the existing baseline index passed (13 chunks, 768 dimensions), with no model calls or index writes.

## Review packages

[Golden review](../docs/golden-review.md) presents all 12 original questions, unchanged labels, exact evidence, document IDs, chunk/line/character locations, rationale, and blank human approval/correction fields. The compound and warranty cases include contextual evidence without modifying their original expected quotes. The reset case explicitly invites review of whether additional procedure steps must be mandatory.

[Holdout review](../docs/holdout-review.md) presents 12 fixed synthetic cases and five validator-output probes. There are 29 fictional documents and 33 chunks across 11 isolated scenarios. Coverage includes long and adjacent chunks, agreeing sources, genuine conflicts, missing facts, partial compounds, cross-chunk document injection, irrelevant exact sentences, version/authority uncertainty, low-overlap paraphrases, duplicate crowding, invented IDs and altered/partial quotes. Fixtures/labels were frozen before offline execution. They are agent-authored, inspected during implementation, and await independent owner review; they are not a blind human acceptance set.

## Reproduced defects and changes

| Baseline issue | Offline hardening |
| --- | --- |
| Stored IDs, offsets, title/version/topic and FTS text could disagree with source without detection. | Validate bounded schema, metadata, source hashes, header fields, deterministic chunks/offsets, vectors, and FTS correspondence before any model access; reject inconsistent indexes with 503. Tests cover 21 distinct tampering operations and a replaced table/view. |
| Chunk-local punctuation could authorize a source-sentence tail at a hard boundary. | Derive full sentences from complete source paragraphs; require the entire sentence inside the actual retrieved chunk. Preserve soft line breaks and decimal numbers. |
| Every pair of same-topic documents caused abstention, including identical agreeing facts. | Allow only the same version and an identical nonempty ordered sequence of complete sentences; retain conservative ambiguity otherwise. |
| Repeated chunks could consume semantic/keyword candidate slots. | Canonicalize exact chunk text before candidate caps, retain provenance, and use stable ID tie-breaking. Fifteen duplicate notes no longer crowd out the actual policy. |
| A model could return a literal sentence covering only the first clause of a compound question. | Check lexical coverage of each conjunction-separated clause before and after generation; reject the entire incomplete selection. |
| Explicit document instructions could enter the model context. | Quarantine the complete source document for recognized model-control patterns, including instructions in a different chunk from the requested fact. Never send quarantined passages to generation or accept them as citations. |
| A count question could accept a source sentence about card color due to generic token overlap. | Apply a conservative numeric shape check; exact-source presence is still recorded separately from semantic correctness. |

All existing exact-ID, exact-quote, finite-vector, request/context/token/time and read-only checks remain. There is no semantic repair, unsupported-claim rewriting, new tool capability, model download, new dependency, or architecture migration.

## Offline results

- pytest: **66 passed, 1 opt-in actual-inference test skipped**. Unmocked HTTP is blocked by an autouse fixture, with an explicit test confirming the block. A cross-platform preservation regression checks Git LF versus original local CRLF bytes while still rejecting content edits.
- Vitest: **3 passed**. TypeScript/Vite build: **PASS**.
- Playwright: **4 synthetic desktop/mobile tests passed, 2 actual-model project variants skipped**. Original screenshot output was untouched.
- [Scripted holdout report](holdout-offline.json): **11/12** scripted outcomes match their fixed expected labels, and all synthetic answering databases remained unchanged. **One known false abstention remains: `lexical-paraphrase`.** Its supported label was not altered. Constant vectors and oracle/scripted selections test guards only; these are not real retrieval recall or generation correctness scores.
- Final exact-SHA GitHub Actions status is reported with the delivery commit. CI uses no Ollama service.

## Remaining defects and limits

The exact-quote validator proves source membership, not full semantic entailment or source truth. Numeric shape and clause overlap are necessary heuristics: irrelevant numeric sentences, subtle missing conditions, or compound intents without recognizable conjunctions can still evade them. Legitimate qualitative answers can be over-rejected. Regex quarantine has false positives and bypasses; it does not establish comprehensive injection resistance. A coherently rewritten source/index/hash set cannot be authenticated by internal consistency checks alone.

The supported beacon/indicator paraphrase still fails the per-quote lexical gate. Agreement with different wording remains conservatively ambiguous. Exact duplicate collapse does not handle near-duplicates or remove duplicate influence from BM25 global statistics. Broader semantic judging, authority policy, and retrieval changes need separate evidence; labels must not be rewritten to hide these limits.

## Pending Phase D proposal

Owner approval is required for the exact pushed code and reviewed labels (SHA-256 in the review document). Proposed ceiling: **12 actual answer-generation POSTs + 23 embedding POSTs** (11 ingestion batches for 33 chunks, 12 queries), **one attempt per question, zero retries or extra smoke calls, 20 minutes total**. Reuse only the pinned baseline nomic/qwen digests; verify identity before any inference and abort on mismatch. No downloads, substitutions, paid services or baseline reruns.

The predeclared recording/scoring protocol separates exact-source citation validity from semantic correctness and includes recall@6, unsupported false positives, correct/incorrect abstentions, supported false abstentions, injection outcomes, per-case latency, actual attempted calls and explicit failure categories. Live metrics remain **UNVERIFIED** until authorized execution. No live holdout runner was added during this offline milestone.
