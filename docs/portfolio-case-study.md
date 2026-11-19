# Portfolio Case Study: Local RAG Customer Support Agent

**Project 5 is portfolio complete. Production container acceptance is deferred.**

## Problem and solution

Support questions often need exact instructions, policy dates, or an explicit
“I don't know.” A plausible answer can be worse than no answer if it cites the
wrong product, ignores a conflicting bulletin, or repeats instructions embedded
in a document. The project uses a fictional product, LumaDock, to build a
support assistant that can show exactly what it used and abstain when the
evidence is insufficient. It runs locally and does not act on customer systems.

## Architecture

```text
Local Markdown/text -> ingestion CLI -> SQLite documents, chunks, vectors + FTS5
React/TypeScript    -> FastAPI /api/ask -> hybrid retrieval -> local Qwen selection
                                               |            -> citation/relevance checks
                                               +------------> quoted answer or abstention

Optional investigation -> LangGraph -> five read-only MCP tools -> evidence + trace
```

**FastAPI and React.** FastAPI provides a bounded local ask endpoint. The
React/TypeScript interface presents the answer, cited passages, document
versions, and retrieval diagnostics. The HTTP surface does not expose ingestion,
arbitrary file reads, outbound actions, or document mutation. The application
is designed for local, single-user operation.

**Ingestion and retrieval.** The CLI restricts ingestion to selected local
Markdown/text files. It normalizes text while retaining original offsets,
assigns stable document and chunk IDs, and stores version and provenance data.
Nomic embeddings use separate document/query prefixes. SQLite stores the
vectors and FTS5 index. Query-time retrieval combines cosine similarity and
FTS5 rankings with equal-weight reciprocal rank fusion (`k=60`), then bounds
the returned evidence. Answering opens SQLite read-only. The lifecycle CLI
adds preview and confirmation for mutations, atomic index changes, and reuse
of unchanged compatible embeddings. A [bounded real lifecycle run](../reports/lifecycle-real-20261117-once/report.md)
verified reuse and offsets across add/update/delete using three embedding POSTs.
It did not test generation.

**Grounding and prompt-injection defense.** Local Qwen proposes IDs and exact
complete sentences from the retrieved passages. The backend checks membership,
citation offsets, relevance, consistency, and requested clause coverage.
Entity/facet checks distinguish similar-looking facts about separate products;
documented aliases can support narrow paraphrases. Retrieved documents are
untrusted input. The application can quarantine instruction-like documents
rather than letting them supply an answer. Ambiguity, unresolved conflicts,
unsupported selections, and invalid citations fail closed. These controls
are bounded checks, not proof against every prompt injection or evidence that
the underlying documents are true.

**LangGraph and MCP.** The optional [investigation flow](investigation.md)
uses typed, bounded graph steps and an allowlist of five read-only MCP tools.
It assembles provenance and exposes a deterministic trace. Tool output cannot
grant privileges or authorize mutations. A [scoped live query acceptance](../reports/investigation-nomic-4b-once/report.md)
used one Nomic ingestion batch and two query embeddings: a supported question
returned an exact citation and an unsupported one abstained. It made zero
generation calls. This demonstrates real local query embeddings through the
investigation stack, not broad retrieval or autonomous planning quality.

## Evaluation and test evidence

| Evidence | Result | Boundary |
| --- | --- | --- |
| [Frozen 12-case live adversarial evaluation](../reports/phase-d-v2-once/report.md) | 23 embedding POSTs, 8 generation POSTs; 100% recall@6 on 10 applicable cases, 9/9 exact-source citations, and 9/12 rubric-correct outcomes | A small agent-curated fictional evaluation; not a fully independently human-reviewed benchmark |
| [Offline replay and new holdout](../reports/offline-relevance-hardening/report.md) | 10/12 historical replay outcomes and 17/17 new synthetic holdout outcomes | Frozen recorded selections or synthetic inputs; no live model rerun |
| [Reviewed-source Docker-free CI](https://github.com/mubidotjs/local-rag-customer-support-agent/actions/runs/44445068571) | 664 full-stack Python tests passed per Python version, plus frontend build, four Vitest tests, and four synthetic Playwright tests | No live model traffic or real application container acceptance |

Exact citations and relevant answers are separate measures. In the live run,
two incorrect answers included exact source quotes about unrelated entities.
Offline hardening rejected those mixed selections and accepted a previously
rejected supported paraphrase. The two mixed selections still scored as
false abstentions under the frozen rubric; no new correct generated answer was
demonstrated.

## Docker and Unix-socket ingress

The original [Milestone 5B attempt](../reports/container-runtime-5b-once-consumed/README.md)
stopped when host TCP ingress was refused despite an internal Nginx health
success. Docker recorded the requested loopback binding without an effective
publication on the internal bridge. The [isolated v4 diagnostic](../reports/container-port-diag-5b-v4-once-complete/README.md)
used fictional responders: internal HTTP and Ubuntu Unix-socket HTTP both
returned 200, the cross-bridge TCP control was denied, and all six cleanup
commands succeeded. The Ubuntu published-port HTTP request was skipped because
there was no effective mapping. It did not start the RAG application or probe
Windows localhost.

The revised [private Unix ingress design](private-unix-ingress.md) keeps the
application on its internal bridge. An unprivileged host proxy binds to IPv4
`127.0.0.1:5173` and sends requests only to a dedicated private Nginx Unix
socket. Nginx reaches the backend through their shared namespace loopback.
The SQLite bind mount stays read-only, and the proposed acceptance runs with
models disabled. Host/Origin validation, input limits, socket ownership, and
peer checks are tested with synthetic transports. Loopback reachability is not
user authentication. Real Nginx/container startup, the full same-origin
application flow through the proxy, persistence after a container restart,
and Windows browser access were not validated end-to-end.

**Milestone 5B runtime acceptance: DEFERRED. Milestone 5C: DEFERRED.**
The [readiness review](private-ingress-runtime-readiness.md) found that Ubuntu
already assigns UID 101 to `landscape`, while the frozen frontend uses UID 101
for private socket ownership. This is an environment compatibility limitation,
not a portfolio blocker. A compatible isolated host with UID 101 unused, or a
separately reviewed frontend build using a verified unused UID, could resolve
it without changing Ubuntu's `landscape` account. No public deployment or
production acceptance is claimed.

## Tradeoffs and lessons

- **SQLite simplicity vs. scale.** One local index keeps storage and provenance
  inspectable. Exhaustive vector similarity is appropriate for the bounded
  corpus; it is not a claim about large-corpus throughput.
- **Extractive answers vs. flexibility.** Exact sentences and narrow relevance
  rules make the evidence auditable, at the cost of legitimate paraphrases
  and questions that sometimes abstain.
- **Read-only tools vs. autonomous actions.** LangGraph and MCP make evidence
  access inspectable. They add orchestration without expanding the system into
  browsing, customer communications, or writes.
- **Isolation vs. convenience.** Network isolation changes what a host
  port binding actually means. The synthetic socket diagnostic narrowed the
  problem without weakening the bridge or presenting a test as production
  proof.

The portfolio closure adds documentation only. Historical reports, claims,
frozen plans, and test artifacts are retained unchanged.
