# Offline entity/facet relevance and paraphrase hardening

**Real-model status: NOT RERUN.** Zero embedding, generation, model metadata, download or live-browser requests were made. Historical Phase D permission remains consumed; no new live budget is requested or implied.

## Evidence and method

Baseline: `7bc42b194158f1f672b7ee3d48f2b7a5faafb503`. All frozen v2 labels, historical Phase D files, prior reports, model evidence, browser captures and existing indexes are unchanged. The [preservation snapshot](../../evaluation/relevance/preservation.json) pins **139 original artifacts**, including the 12 existing SQLite indexes and the consumed claim. No old database was used for replay: exact recorded passages were rechecked against preserved source files and fed to the current answer path with the recorded selections. A selection absent from history cannot be invented by the replay adapter.

Replay reuses the actual recorded model output unchanged. It neither retrieves again nor calls a model. A single irrelevant claim rejects the whole selection; valid claims are not salvaged from mixed output. Accepted answers contain the same original complete source sentences, with their original offsets and retrieved IDs. New JSON evidence chains identify the specific entity, facet, source alias/procedure anchors and bounded action mapping. The UI can disclose those supporting source sentences.

## Deterministic defects addressed

- **Entity binding:** finite source-relation templates associate the subject, predicate/facet, object/count and value. Proper names use whole-token equality, not substring or embedding similarity. Extra product qualifiers cannot be silently dropped. One narrow count-template scope rule permits omitting a controlled request purpose (recovery/refund/return/renewal) only for a unique matching request context. Competing request scopes abstain.
- **Facet binding:** validity/policy durations, release conditions, issued/permitted counts, return/refund timing, support/automatic policies, configuration options/actions and eligibility conditions have distinct patterns. A count for one object cannot answer a count for another; a number alone never establishes relevance. Every emitted sentence needs a proof, and every requested clause needs coverage.
- **Explicit aliases:** only documented control-name identities in safe, actually retrieved complete sentences can form a one-hop, one-to-one, same-document link. Bare control heads may resolve only through a unique explicit link. Physical proximity, similarity, negation, another product document, ambiguous definitions and multi-hop associations do not authorize equivalence.
- **Bounded action wording:** mute/silence, enable/activate and disable/deactivate are the only synonym groups. Reset/export are literal actions. These mappings do not rewrite the quoted answer, and unrelated actions remain unsupported. The existing adjacent reset-button release step can cite its immediately preceding explicit reset procedure as support.
- **Fail closed:** unknown grammar, unresolved/pronominal entities, competing scopes/values/conditions, absent alias evidence, quarantine, manipulated citations and altered/partial quotes abstain. Existing integrity, conflict, exact sentence, similarity floor, read-only storage and inference limits remain. No semantic repair or unrestricted semantic judge was added.

## All 12 historical Phase D cases — offline replay

| Case | Historical outcome | Current replay | Reason | Frozen-rubric outcome |
| --- | --- | --- | --- | --- |
| `late-amber` | incorrect | safe rejection of mixed output | `entity_mismatch` | Expected answer still absent |
| `adjacent-cobalt` | incorrect | safe rejection of mixed output | `entity_mismatch` | Expected answer still absent |
| `agreeing-sources` | correct | accept original quote | `verified_exact_quotes` | PASS |
| `conflicting-coverage` | correct | abstain | `conflicting_sources` | PASS |
| `missing-fee` | correct | abstain | `answer_shape_mismatch` | PASS |
| `partial-compound` | correct | abstain | `incomplete_evidence` | PASS |
| `document-injection` | correct | abstain | `untrusted_source` | PASS |
| `irrelevant-sentence` | correct | abstain | `answer_shape_mismatch` | PASS |
| `authority-uncertain` | correct | abstain | `conflicting_sources` | PASS |
| `lexical-paraphrase` | incorrect | accept original quote | `verified_exact_quotes` | PASS |
| `duplicate-crowding` | correct | accept original quote | `verified_exact_quotes` | PASS |
| `exact-token-policy` | correct | accept original quote | `verified_exact_quotes` | PASS |

**All nine previously passing cases retain their expected outcome.** Both historical cross-entity selections are rejected in full, and the recorded supported paraphrase now passes. The frozen answer/abstention rubric therefore yields **10/12**, with **two supported false abstentions** (`late-amber`, `adjacent-cobalt`) and **zero unsupported answers**. Those two refusals are safety improvements, not newly correct generated answers. The validator accepts a correct target-only quote in focused tests, but offline validation cannot show that a future model will select it.

The paraphrase evidence chain is: “How can I silence the beacon?” → actual source “The Lyra beacon is the panel indicator.” → the bounded silence/mute mapping → original quote “To mute the indicator, enable quiet mode.” Both source sentences retain actual chunk `lyra-controls:c000`, version and offsets. No extra claim is inserted into the answer.

## New frozen synthetic holdout

[Labels and inline fictional documents](../../evaluation/relevance/holdout.json) were frozen before this milestone's first offline execution. SHA-256: `44303ab7163414f98bdc688971f8bcf2a214fa04f9354dc091060875d840906e`. This is a separately authored, agent-curated set with different entities/wording, not a blind independently human-authored benchmark. Its expected outcomes were not obtained from a model or changed to match the implementation.

**17/17 expected outcomes pass**: four supported selections accepted and thirteen adversarial selections rejected. No false abstentions or unsupported acceptances occur in this bounded set.

| Case | Expected | Observed | Reason |
| --- | --- | --- | --- |
| `specific-duration` | accept | accept | `verified_exact_quotes` |
| `extra-other-duration` | abstain | abstain | `entity_mismatch` |
| `ambiguous-token` | abstain | abstain | `ambiguous_entity` |
| `duration-versus-count` | abstain | abstain | `facet_mismatch` |
| `issued-key-count` | accept | accept | `verified_exact_quotes` |
| `wrong-count-object` | abstain | abstain | `facet_mismatch` |
| `altered-count` | abstain | abstain | `unsupported_quote` |
| `documented-action-alias` | accept | accept | `verified_exact_quotes` |
| `other-product-control` | abstain | abstain | `entity_mismatch` |
| `association-is-not-alias` | abstain | abstain | `entity_mismatch` |
| `ambiguous-alias` | abstain | abstain | `ambiguous_alias` |
| `unsupported-action` | abstain | abstain | `facet_mismatch` |
| `policy-condition` | accept | accept | `verified_exact_quotes` |
| `partial-compound` | abstain | abstain | `incomplete_evidence` |
| `fabricated-citation` | abstain | abstain | `unverifiable_citation` |
| `quarantined-alias` | abstain | abstain | `untrusted_source` |
| `conflicting-duration` | abstain | abstain | `conflicting_relation` |

Additional regression checks cover whole-name order and Pro/Plus-style qualifier differences, pronouns, ambiguous request scopes, missing/repeated/multi-hop aliases, cross-chunk alias support, mixed duration/action questions, conflicting scalar values/conditions, numerical alterations, complete reset provenance, existing supported golden answers, malicious source text, read-only answering and all prior security tests.

## Limits and remaining work

The two historical mixed outputs still produce abstentions, not correct answers. This is explicitly not a 12/12 live-model result. Source sentence membership and relation proofs do not establish source truth. The recognizer intentionally covers finite English templates and ASCII-word entity phrases; unrecognized but legitimate questions/paraphrases can still abstain. It does not infer arbitrary aliases, do multilingual understanding, reconcile distinct conditional policies, or prove arbitrary multi-step task completeness.
Aliases must actually be retrieved, be in the same document as the answer, and be unambiguous; missing/ambiguous links can cause conservative refusals. No synonym expansion changes FTS5 or semantic rankings. Previously recorded retrieval scores are historical, and no new retrieval recall claim is made. The lexical query prefilter remains a necessary pre-generation gate, supplemented only by explicit relation proofs; numeric checks are facet-specific so a configuration step in a mixed duration/action question is not wrongly required to contain a number.
A future live verification would need its own separate owner-approved plan. None is requested or exercised in this task. Document lifecycle, orchestration and deployment remain out of scope.

## Validation and preservation

Offline pytest, Vitest, TypeScript/Vite build and synthetic desktop/mobile Playwright results are reported with the delivery commit and exact-SHA CI. Real-model tests remain disabled. [replay.json](replay.json), [holdout.json](holdout.json) and [audit.json](audit.json) are new artifacts; no historical file or score was replaced. All 139 protected local artifact hashes were verified after testing.
