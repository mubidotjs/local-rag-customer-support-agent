# One bounded Phase D evaluation

**Completed once:** [reviewed results and full evidence](../reports/phase-d-v2-once/report.md). The persistent claim remains consumed. This document records the enforced protocol; it does not authorize rerunning the command. Four unused generation slots do not permit another batch.

The owner explicitly authorized **one** live run against application baseline `a9f30ecd7cc2069e63c048bae6f5a668d8f18a61` and immutable v2 label SHA-256 `b7a4600f21bf2e030390ecb7b749c5c2d9ac6feb9fc826250de7665baa1a22e6` in the current Codex conversation. This is a new execution authorization; the historical label-finalization approval and frozen v2 budget files remain unchanged.

This run is an **agent-curated synthetic adversarial evaluation**, not an independently human-validated benchmark. Only the 12 frozen holdout questions are evaluated. The golden questions are not run. The five adversarial output probes remain offline tests, not extra generation calls.

## Limits and persistence

- At most 11 ingestion embedding POSTs, 12 query embedding POSTs (23 embeddings total), and 12 generation POSTs. One attempt per case and one batch per scenario; failures consume attempts. No retry or resume path exists.
- Twenty-minute execution clock includes preflight, identity verification, ingestion, answering and scoring. Each inference HTTP request has at most 90 seconds or the remaining global time, whichever is less. A separate hard-deadline watchdog exits the runner at the global deadline, leaving durable checkpoints and the pre-send journal. Disconnecting does not guarantee immediate cancellation of computation already running inside Ollama.
- Only fixed loopback `/api/embed` and `/api/chat` POSTs are permitted. Exactly two startup metadata GETs (`/api/tags`, `/api/version`) verify the two pinned local model identities. No model pulls, substitutions, cloud inference, tools, prompt changes or smoke/baseline questions occur.
- Existing identities: `nomic-embed-text:latest` / `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`; `qwen3:4b` / `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`.
- `.local/phase-d-v2-once.claim.json` is created exclusively before any HTTP request. It is never removed or reused. A second invocation is rejected even if the first stopped early. Fresh indexes for all 11 scenarios are retained under `.local/phase-d-v2-once/indexes/`; existing indexes are never reused or altered.
- Results, requests, response metadata/raw generation outputs, and an append-only fsynced attempt ledger are written under a fresh `reports/phase-d-v2-once/`. Embedding vectors remain in the isolated local databases; published embedding receipts include dimensions, counts, durations and a parsed-response hash.
- A fatal budget/model/integrity/deadline boundary stops execution. Partial checkpoints are retained; failed questions are never retried. Once results are available, reporting and offline audits may read them without issuing more model calls.

The runner refuses execution without its explicit one-run flag. Ordinary CI tests its counters, identity checks, pre-send ordering, timeout and duplicate-run protections using synthetic transports. Neither CI nor tests may run the real CLI against the user's run directory. Do not delete the claim or change the run ID to reuse this authorization.

## Fixed scoring

Recall@6 is macro recall of frozen source-chunk evidence groups. Identical approved quotes are alternative supporting sources; different conflicting quotes form separate groups. Cases without labeled evidence are N/A. Actual retrieved IDs, source offsets and exact quote inclusion determine a hit; duplicate aliases do not become citeable IDs.

Exact citation validity checks the actual retrieved passage, document ID/version, complete quote and both source coordinate systems. Zero emitted citations has an N/A denominator. Raw model claims are checked separately, including rejected claims. A correct source location alone does not imply a semantically correct answer.

Grounded-answer correctness uses the frozen finite rubric: the expected abstention decision, all required supported answer facts, no extra sentence outside that fact set, and exact-valid citations. It is an agent-curated rubric score, not an independent human semantic judgment. False-positive answers, supported false abstentions, missing facts, conflicts, injection/quarantine decisions, latencies and failure reasons are retained per case. The supported paraphrase and security-quarantine labels remain unchanged even if the system fails.

Application code, prompts, label bytes and historical evidence must match the authorized baseline. The runner itself may be committed as an additive descendant; the report records both that runner SHA and the exact unchanged application baseline. Offline tests must pass before the sole live invocation. No tuning or second inference batch is authorized after observing results.
