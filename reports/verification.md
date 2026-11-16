# First milestone verification — 2026-11-16

## Actual local inference

- Existing Ollama 0.40.16 was discovered through its loopback service. Its existing `qwen3:4b` installation was reused without downloading or modifying it.
- The user explicitly authorized downloading `nomic-embed-text`. The completed download was 274,302,450 bytes. The application itself has no model-download path.
- Real ingestion produced **13 documents, 13 chunks, 768 dimensions**, persisted in local SQLite with FTS5 and offset metadata.
- The initial real generation returned an invalid citation; the verifier rejected it. The final implementation restricts model choices with structured-output enums and still validates every quote independently.
- The bounded [12-case actual-model evaluation](local-evaluation.json) passed: labeled document recall@6 **1.0**, grounded-answer correctness **12/12**, abstention decisions **12/12**, and citation validity **100% across 8 emitted citations**. It performed 12 real query embeddings and 9 real generations; three questions were rejected by pre-generation evidence gates. The database hashes before and after answering matched.
- One additional Playwright test passed through the real browser → Vite proxy → FastAPI → read-only SQLite → local embeddings → local qwen3:4b path. The reset answer cited `reset:c000` and included the documented 10-second procedure. Desktop and 390-pixel mobile screenshots were visually inspected, with no horizontal overflow.

## Synthetic checks

- pytest: **25 passed, 1 actual-inference test skipped**.
- Vitest: **3 passed**.
- Playwright: **4 passed**, covering synthetic desktop/mobile answer/citation/diagnostics, safe text rendering, and explicit abstention. Real-model tests are opt-in and skipped in ordinary CI.
- TypeScript and Vite production build: **PASS**.
- npm dependency audit after updating the test runner: **0 vulnerabilities**.
- Total HTTP inference deadline, embedding response validation, fake citation rejection, source-offset consistency, corpus bounds, read-only answering, and hostile origin/host rejection are checked without Ollama.

The exact pushed commit's GitHub Actions status is reported separately; this file does not imply that CI ran real models. Local Python was 3.15.2; CI targets 3.12 and 3.13. This is a narrow synthetic-product MVP, not general production support acceptance. Source truth, broader adversarial evaluations, and conservative same-topic ambiguity behavior remain explicit limitations.

The golden cases were curated independently of the model under test by the coding agent. They have **not** received human authorship/review sign-off; that part of the requested acceptance process remains open.
