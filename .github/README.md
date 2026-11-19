# Local RAG Customer Support Agent

**Portfolio complete. Production container acceptance deferred.**

A local support assistant for LumaDock, a fictional product. Ask a question in
the React interface and get an answer with verifiable source citations, or an
explicit abstention when the documents do not support an answer. FastAPI,
SQLite/FTS5, and Ollama run locally. No hosted inference, API keys, or external
vector store are required.

[Portfolio case study](../docs/portfolio-case-study.md) ·
[Architecture](../docs/architecture.md) ·
[Document lifecycle](../docs/document-lifecycle.md) ·
[Investigation tools](../docs/investigation.md)

## What it does

- Ingests local Markdown and text, preserving versions, stable chunk IDs, and
  exact source offsets. CLI lifecycle operations require preview and explicit
  confirmation; compatible chunks reuse their embeddings.
- Combines Nomic embedding similarity with SQLite FTS5 keyword ranking using
  reciprocal rank fusion. The UI exposes passages, ranks, and citations.
- Uses local Qwen to select complete source sentences, then independently
  checks their provenance and relevance. Conflicting, incomplete, or unsafe
  evidence can cause abstention.
- Offers an optional LangGraph investigation flow with five bounded, read-only
  MCP tools. It can return deterministic evidence without a generation call.

## Run locally

Use Python 3.12+ and Node.js 24. The demo requires an owner-operated Ollama on
`127.0.0.1:11434` with `nomic-embed-text:latest` and `qwen3:4b` already
installed. The application does not download models. The following demo uses
real local inference; the tests below do not.

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m support_agent.cli corpus
.venv/bin/python -m uvicorn support_agent.api:app --host 127.0.0.1 --port 8000 --workers 1
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.
In a second terminal, run `cd frontend`, `npm ci`, and `npm run dev`.
Open **http://127.0.0.1:5173**. Try “How do I factory reset LumaDock Home?”
or inspect the source evidence for a warranty question with conflicting sources.

## Evidence and scope

| Area | Verified scope |
| --- | --- |
| Local RAG | [Recorded live evaluation](../reports/phase-d-v2-once/report.md): 9/9 exact-source citations and 9/12 correct outcomes on a small fictional adversarial set |
| Offline grounding | [Replay and holdout](../reports/offline-relevance-hardening/report.md): 10/12 historical outcomes and 17/17 synthetic holdout outcomes; no model rerun |
| Lifecycle and investigation | [Live lifecycle acceptance](../reports/lifecycle-real-20261117-once/report.md) and [live Nomic query acceptance](../reports/investigation-nomic-4b-once/report.md) passed in their narrow scopes |
| Containers | [Synthetic Docker diagnostic](../reports/container-port-diag-5b-v4-once-complete/README.md) verified HTTP over a private Unix socket on Ubuntu WSL2; the actual application stack and Windows browser ingress were not validated end-to-end |

Ordinary tests run without Ollama. With the relevant dependencies installed,
use `python -m pytest -q --deselect=tests/test_container_packaging.py::test_compose_static_security_and_uds_override`
for Docker-free Python tests. In `frontend/`, use `npm test`,
`npm run build`, and `npm run test:e2e` with Playwright Chromium installed.
Leave both real-model opt-in flags, RUN_REAL_OLLAMA and RAG_REAL_E2E, unset. The
[reviewed implementation CI](https://github.com/mubidotjs/local-rag-customer-support-agent/actions/runs/44445068571)
covers the Python matrices, frontend build, Vitest, and synthetic browser tests.

Milestone 5B production runtime acceptance and Milestone 5C are **deferred**.
The [private ingress design](https://github.com/mubidotjs/local-rag-customer-support-agent/blob/98475535bb96a28710a4e3bd918ed641795412b8/docs/private-unix-ingress.md) and offline
coordinator are retained. This is a local, single-user portfolio project;
there is no public deployment.

The [original README](../README.md) is preserved as part of the historical
record. This README reflects the final portfolio scope.
