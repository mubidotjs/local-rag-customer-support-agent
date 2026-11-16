# Local RAG customer support agent

A local-first support assistant for **LumaDock**, a fictional desk hub. FastAPI + React/TypeScript, SQLite + FTS5, real `nomic-embed-text` embeddings, and local `qwen3:4b` generation through Ollama. No API keys, hosted models, external vector stores, customer messages, or deployment.

This is Project 5, separate from the Production AI Knowledge Agent. It does not modify or manage other projects.

## Run locally

Requirements: Python 3.12+, Node.js 24, and an already running local Ollama at `http://127.0.0.1:11434`. Inspect your installation first:

```powershell
ollama list
# Alternatively, if Ollama is running but not on PATH:
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

Both `qwen3:4b` and `nomic-embed-text:latest` must be installed. **Do not reinstall qwen3:4b. Obtain the machine owner's authorization before downloading an additional model.** Only after authorization, use `ollama pull nomic-embed-text`. Nothing in this application downloads models automatically.

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.lock
.\.venv\Scripts\python -m support_agent.cli corpus
.\.venv\Scripts\python -m uvicorn support_agent.api:app --host 127.0.0.1 --port 8000 --workers 1
```

In another terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Ask “How do I factory reset LumaDock Home?” or “How many days after delivery may I return LumaDock Home?” Expand retrieval diagnostics to inspect the scores, ranks, complete source chunks, versions, and offsets. The warranty question deliberately abstains because two active bulletins conflict.

On macOS/Linux use `.venv/bin/python` in place of `.\.venv\Scripts\python`. No environment file is required. `RAG_DB` optionally selects a different local SQLite index; set it in both ingestion and API terminals. The fixed inference endpoint and model names are in `support_agent/config.py`.

## What the MVP does

1. The **CLI** reads only regular UTF-8 `.md`/`.txt` files directly inside an explicitly selected local directory. Files require the metadata header shown in `corpus/`. There are 13 human-authored fictional documents, including two deliberately conflicting warranty bulletins.
2. Normalize CRLF/CR to LF, tabs to spaces, and remove an initial BOM. Retain the original decoded source and normalized text. No HTML rendering, Markdown execution, Unicode rewriting, URL fetching, recursive traversal, or remote imports.
3. Split into at most 800-character nonoverlapping chunks, preferring newline or sentence boundaries in the second half of each window. Trim chunk-edge whitespace. Stable chunk IDs are `<document-id>:c000`, `c001`, etc. Store title, version, topic, filename, SHA-256, chunk ordinal, raw-source and normalized boundaries. All offsets count **Unicode code points**, starting at zero, end exclusive; raw offsets refer to UTF-8 decoded original text, not byte offsets.
4. Prefix passages with `search_document: ` and queries with `search_query: ` for nomic. Request actual local embeddings, validate finite nonzero vectors, normalize them to unit length, and persist JSON vectors in SQLite. The sample corpus produces 13 chunks with 768-dimensional vectors.
5. During answering, open SQLite with `mode=ro` and `PRAGMA query_only=ON`. Exhaustively calculate cosine similarity across the bounded index and run parameterized FTS5/BM25 keyword search. [Retrieval and safety details](docs/architecture.md).
6. Fuse the top 20 semantic and keyword rankings using **equal-weight reciprocal rank fusion**, `score(c) = 1/(60 + semantic_rank(c)) + 1/(60 + keyword_rank(c))`. Missing ranks contribute zero. Both rankings and fused ties use ascending chunk ID. Return at most six chunks and 4,800 context characters. This is reproducible for a fixed index/query embedding; model runtimes may introduce floating-point variation.
7. Make one bounded `qwen3:4b` `/api/chat` call per eligible question. Its structured schema restricts choices to actual retrieved IDs and exact complete sentences. The backend independently validates every selection. Answers contain only verified copied sentences with `[document-id:chunk-id]` labels; **this MVP is intentionally extractive**.
8. Abstain on insufficient relevance, ambiguous active sources, invalid model output, fabricated IDs, altered/partial quotes, or generation failure. Model unavailability during retrieval returns an actionable HTTP 503. No answer cache, conversation memory, customer records, or writes during answering.

Identical ingestion is a no-op without embedding requests. Changed corpora are rejected against an existing index. Update/delete/reindex operations are outside this milestone; select a fresh `RAG_DB` for a separate corpus.

## Verification

Ordinary tests **do not require Ollama** and never claim real inference acceptance:

```powershell
.\.venv\Scripts\python -m pytest -q
cd frontend
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

The backend's `SyntheticModel`, Vitest HTTP responses, and regular Playwright routes are explicitly synthetic. They verify plumbing, bounds, read-only storage, citation rejection, safe source rendering, and desktop/mobile behavior. GitHub Actions runs these on every push, with Python 3.12/3.13, without installing or starting Ollama.

The optional real browser test uses the running FastAPI backend with no intercepted responses:

```powershell
# Keep the API running, then in frontend/:
$env:RAG_REAL_E2E = '1'
npx playwright test e2e/real-local.spec.ts --project=desktop
```

It makes one actual question request and captures desktop/mobile screenshots in the ignored `frontend/test-results/` directory. Ordinary CI skips it.

To opt into actual local inference, first ingest the corpus, then run from the root:

```powershell
.\.venv\Scripts\python -m evaluation.run --generate --limit 12
# Optional isolated smoke test (makes additional real local model requests):
$env:RUN_REAL_OLLAMA = '1'
.\.venv\Scripts\python -m pytest tests/test_real_ollama.py -q
```

The fixed, independently curated [golden cases](evaluation/golden.json) cover supported, unsupported, conflicting, compound, and prompt-injection questions. Expected answers were specified independently of qwen3 output; human review and sign-off are still pending. The bounded evaluator makes one embedding per question and at most 12 generations, prints progress, saves model digests and per-case evidence, and exits nonzero if any golden answer fails. Omitting `--generate` measures actual retrieval only. [Local evaluation report](reports/local-evaluation.json) records actual local inference, explicitly separate from synthetic tests. Exact quote matching is a small-corpus correctness measure, **not** a general semantic judge or proof against all injections.

## Limits and scope

The server binds only to loopback; use one worker. It rejects unrelated Host/Origin values, limits request bodies and question length, and admits one model operation at a time. No upload, document-read-by-path, URL fetch, tool execution, remote action, or customer messaging endpoints exist. The local owner chooses the trusted corpus; retrieved content remains untrusted model input. Do not expose this development server publicly.

The MVP favors abstention. Synonyms without lexical overlap can fail the relevance gate; same-topic documents are conservatively ambiguous even when they agree. Exact copied sentences prove where emitted claims came from, but cannot establish source truth or guarantee that every possible question was fully understood. A single-process lock is a resource bound, not a multi-user queue. All limits and remaining work are documented in [architecture](docs/architecture.md).

Later milestones remain out of scope: document mutation/reindexing, rerankers and retrieval tuning, LangGraph, MCP investigation tools, comprehensive injection evaluation, Docker, and any public synthetic showcase.
