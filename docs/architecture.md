# Architecture and trust boundaries

```text
CLI -> validate local files -> normalize + source map -> deterministic chunks
    -> fixed loopback Ollama /api/embed -> atomic SQLite documents/chunks/FTS5/metadata

React -> POST /api/ask -> read-only SQLite + fixed loopback query embedding
      -> cosine + FTS5 BM25 -> RRF -> relevance/ambiguity gates
      -> one local qwen3:4b selection -> exact quote/citation validator -> answer or abstention
```

## Retrieval algorithm

The query is at most 600 characters. Lowercase ASCII alphanumeric query tokens, remove common English function words and fictional product names, deduplicate, sort, and keep the first 32. Feed quoted tokens joined by `OR` to FTS5 through a bound SQL parameter; user FTS operators never execute. Sort BM25 ascending, then chunk ID. Read at most the bounded 500 stored matches, collapse exact duplicate chunk text, then retain 20 unique candidates.

Normalize query and stored vectors, verify dimensions against the index metadata, and take their dot product. Exact duplicate chunk text has one canonical candidate (smallest chunk ID); retain other IDs as diagnostics, without making them citeable. Sort unique semantic candidates descending, then chunk ID; retain 20. Iterate the semantic/keyword union, assign equal-weight reciprocal rank fusion with constant 60, and sort by descending fused score, ascending ID. Select the first six chunks fitting the 4,800-character budget. Scores and both contributing ranks are visible in the UI. SQLite's `unicode61` tokenizer may split words differently from the deliberately simple query tokenizer. Deduplication prevents candidate-slot crowding; repeated documents can still affect FTS5 corpus-level BM25 statistics, and near-duplicates are not collapsed.

For generation, require cosine >= 0.40, an eligible whole source sentence, and either lexical query coverage or an explicit deterministic source-relation proof. The latter can keep an answer passage eligible when its documented alias is in another actually retrieved chunk. Similarity alone never establishes a claim's relevance. Multiple same-topic sources are ambiguous unless every source has the same version and exactly the same nonempty ordered sequence of complete body sentences. Every final citation to an ambiguous topic is rejected, and the highest-ranked eligible ambiguous passage causes pre-generation abstention. There is no automatic conflict resolution or latest-version guessing.

## Grounding contract

The local model returns JSON containing `abstain` and at most three `{chunk_id, quote}` claims. Runtime schema enums contain only eligible retrieved IDs and complete evidence sentences. This follows Ollama's [structured-output API](https://github.com/ollama/ollama/blob/main/docs/api.md). Sentences are located in the complete normalized source body before slicing chunks. A sentence must fit entirely in one retrieved chunk; a fragment at a hard boundary is ineligible. Soft line breaks within a paragraph remain part of the exact quote, and decimal points do not terminate sentences. This deterministic prose parser is conservative, not a complete linguistic parser.

The server independently checks types, keys, actual retrieved IDs, whole-source-sentence equality, duplication, cosine floor, quarantine and ambiguous topics. A finite [entity/facet recognizer](../support_agent/relevance.py) additionally binds every selected sentence to a requested entity, relation and counted object where applicable. Unknown templates, unresolved names, competing scalar values and missing clause coverage abstain. Each requested clause must be covered; any unrelated extra claim rejects the entire answer. Typed facets handle numeric values separately from configuration actions, so one clause cannot impose its numeric shape on an unrelated action clause. The old numeric shape filter remains a necessary rejection check for unrecognized nonanswers; a number alone never authorizes acceptance.

Explicit source aliases are one-hop, one-to-one and document-local, using only safe complete sentences in actually retrieved chunks. Narrow control-name identity patterns and bounded action mappings (mute/silence, enable/activate, disable/deactivate) can establish a paraphrase proof. Mere proximity, similarity, negation, another product's control or ambiguous/multi-hop associations cannot. Entity names otherwise match exactly after article/plural normalization; arbitrary qualifiers such as product Pro/Plus designations cannot disappear. Counted request subjects have one limited purpose-abbreviation rule, permitted only when the request scope is unique. A reset release sentence may cite its immediately preceding explicit reset/hold instruction as a bounded procedure anchor. No unrestricted semantic judge or claim rewriting is used.

Citations retain their original text, document version, chunk ID, and raw/normalized offsets, and add inspectable relevance/support chains. The UI can disclose the source alias/procedure anchors. This finite English grammar is deliberately conservative and does not prove arbitrary semantic entailment, multi-step completeness or source truth. The server builds answers only from unchanged verified quotes and never salvages the valid subset of an invalid model selection.

Question and documents are JSON-encoded untrusted data in a separate user message. The system instruction prohibits following embedded commands, outside knowledge, or tools. Explicit model-control patterns such as instructions to ignore system rules, role markers, or demands to fabricate citations quarantine the entire source document, including other chunks. Detected control instructions in questions also cause abstention. Quarantined passages may be displayed as untrusted diagnostics but are excluded from generation and citation acceptance. No tools are offered, and no model output is executed. React renders all text without `dangerouslySetInnerHTML`. These controls confine capabilities; pattern matching is neither a complete injection detector nor protection against false facts. Corpus selection and human maintenance remain the local owner's responsibility.

Before any query embedding, bounded read-only index validation checks application schema, record/field limits, metadata types and model dimensions, source hashes, header metadata, deterministic chunk identities and both offset systems, complete chunk membership, stored vectors, and exact FTS row correspondence. Altered or missing records fail closed with HTTP 503. This checks internal consistency; it cannot authenticate a coherently rewritten database whose source and hashes were all replaced. Read connections disable trusted schema features and remain `mode=ro`/`query_only`.

## Bounds

| Resource | Bound |
| --- | --- |
| Per-document UTF-8 bytes | 64,000, enforced with a capped read |
| Stored documents / chunks | 50 / 500 |
| SQLite file / embedding JSON field | 64 MB / 120,000 characters |
| Chunk size | 800 normalized code points |
| Embedding batch / dimensions | 16 / 4,096 maximum |
| Question / HTTP body | 600 characters / 4,096 bytes |
| Retrieval candidate lists | 20 semantic + 20 keyword |
| Selected context | 6 chunks / 4,800 characters |
| Model context | 4,096 tokens generation; 2,048 embedding |
| Output | 384 generation tokens, at most 3 exact claims |
| Deterministic relevance | At most 3 requested facets; 6-word normalized entity phrases; 5-word alias endpoints; 1 alias hop |
| Local HTTP connect / total deadline | 3 / 90 seconds per request (180 seconds max for query embedding + generation) |
| Model response | 2 MB maximum streamed into memory |
| Inference concurrency | 1 question per API process, immediate HTTP 429 otherwise |
| Automatic retries / model downloads | zero / zero |

Loopback Ollama calls bypass environment HTTP proxies, disable redirects, use fixed local model names, and request `think=false`, temperature 0 and seed 42. Model context/output bounds and a local request deadline bound normal inference; a client timeout may not immediately stop computation already running inside Ollama. Start only one API worker. The development server has no authentication or internet deployment configuration.

SQLite writes occur only through explicit CLI ingestion or previewed/confirmed lifecycle operations. [Lifecycle details](document-lifecycle.md) describe digest/configuration-bound vector reuse, ordinal/offset remapping, stale-preview rejection, `BEGIN IMMEDIATE` atomic mutations and explicit SQLite recovery. The existing schema's metadata table supports these additions without a table migration. Read-only connections hold an explicit transaction snapshot. Managed lifecycle writes require DELETE journal mode; WAL is rejected. The source body and both offset coordinate systems remain available locally for audit. Answer responses include full retrieved chunks and selected quotes, but no arbitrary file paths. Indexes are local and ignored by Git. The corpus is fictional and contains no customer information.

Legacy model calls retain their original bounds. For future authorized managed embeddings, the pinned adapter makes one bounded metadata GET before and after each embedding POST (each request <=90 seconds, <=2 MB). Thus a managed embedding batch can take up to 270 seconds, with no retries, plus a possible 90-second answer generation request. No managed real calls were made during offline lifecycle acceptance. Query adapters must match a bound index's digest and preprocessing fingerprint before embedding; legacy unbound indexes keep their original querying behavior.

## Evaluation interpretation

Recall@6 is macro recall of independently specified required document IDs across applicable cases, including compound and conflicting cases. Citation validity checks that emitted IDs were retrieved and the quote exactly matches its normalized document offsets. Grounded-answer correctness requires the expected abstention decision and every curated expected quote for supported questions. Human review of these fixed cases is pending. Abstention accuracy includes both accepting supported questions and refusing unsupported ones. Reports also include the number of emitted citations to avoid a vacuous 100% validity claim from an always-abstaining system.

CI is synthetic and verifies contracts without Ollama. Local reports include actual model names/digests, Ollama/Python versions, corpus hashes, raw responses, timings, and before/after SQLite hashes. Hardware and Ollama changes can alter selection quality or timing. The twelve golden cases are a narrow first milestone; broader adversarial tests and semantic/hybrid retrieval comparisons remain future work.

The baseline reports remain immutable historical evidence for commit `c4aaf5696976872e533672c1fb66b93ca3c6a4e0`; they do not certify new hardening changes with real models. [Golden review](golden-review.md), [separate holdout labels and protocol](holdout-review.md), and [offline hardening findings](../reports/adversarial-hardening.md) describe the next milestone. New live inference requires explicit owner approval of labels and budget.

After the historical single Phase D run, [offline relevance hardening](../reports/offline-relevance-hardening/report.md) replays all 12 recorded selections, rejects mixed-entity outputs, and establishes the supported alias chain without a new model request. Its frozen new synthetic holdout and current synthetic tests are separate from the unchanged historical model evidence. Real-model status for these changes is **NOT RERUN**.
