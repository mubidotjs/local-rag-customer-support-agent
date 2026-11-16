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

For generation, require cosine >= 0.40, at least one substantive query token present in a passage, and an eligible whole source sentence. This is a conservative, explicit gate, not a calibrated probability. Multiple same-topic sources are ambiguous unless every source has the same version and exactly the same nonempty ordered sequence of complete body sentences. This narrow agreement exception does not infer semantic agreement between different wording. Every final citation to an ambiguous topic is rejected, and the highest-ranked eligible ambiguous passage causes pre-generation abstention. The corpus deliberately includes a conflicting warranty pair. There is no automatic conflict resolution or latest-version guessing.

## Grounding contract

The local model returns JSON containing `abstain` and at most three `{chunk_id, quote}` claims. Runtime schema enums contain only eligible retrieved IDs and complete evidence sentences. This follows Ollama's [structured-output API](https://github.com/ollama/ollama/blob/main/docs/api.md). Sentences are located in the complete normalized source body before slicing chunks. A sentence must fit entirely in one retrieved chunk; a fragment at a hard boundary is ineligible. Soft line breaks within a paragraph remain part of the exact quote, and decimal points do not terminate sentences. This deterministic prose parser is conservative, not a complete linguistic parser.

The server independently checks types, keys, actual retrieved IDs, whole-source-sentence equality, duplication, lexical overlap, cosine floor, quarantine and ambiguous topics. Citations carry both original-source and normalized offsets. Conjunction-separated question clauses must each have lexical evidence, both before generation and in the selected claims. Count/duration/price questions require a numeric answer shape. These are necessary filters, not a semantic entailment oracle: an irrelevant numeric sentence can still satisfy the shape check, and legitimate qualitative durations can be over-rejected. A single invalid claim rejects the entire selection. The server builds the answer itself using only verified quotes; generated commentary is never exposed. It does not repair or rewrite unsupported selections.

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
| Local HTTP connect / total deadline | 3 / 90 seconds per request (180 seconds max for query embedding + generation) |
| Model response | 2 MB maximum streamed into memory |
| Inference concurrency | 1 question per API process, immediate HTTP 429 otherwise |
| Automatic retries / model downloads | zero / zero |

Loopback Ollama calls bypass environment HTTP proxies, disable redirects, use fixed local model names, and request `think=false`, temperature 0 and seed 42. Model context/output bounds and a local request deadline bound normal inference; a client timeout may not immediately stop computation already running inside Ollama. Start only one API worker. The development server has no authentication or internet deployment configuration.

SQLite writes occur only through explicit CLI ingestion in one transaction. The source body and both offset coordinate systems remain available locally for audit. Answer responses include full retrieved chunks and selected quotes, but no arbitrary file paths. Indexes are local and ignored by Git. The corpus is fictional and contains no customer information.

## Evaluation interpretation

Recall@6 is macro recall of independently specified required document IDs across applicable cases, including compound and conflicting cases. Citation validity checks that emitted IDs were retrieved and the quote exactly matches its normalized document offsets. Grounded-answer correctness requires the expected abstention decision and every curated expected quote for supported questions. Human review of these fixed cases is pending. Abstention accuracy includes both accepting supported questions and refusing unsupported ones. Reports also include the number of emitted citations to avoid a vacuous 100% validity claim from an always-abstaining system.

CI is synthetic and verifies contracts without Ollama. Local reports include actual model names/digests, Ollama/Python versions, corpus hashes, raw responses, timings, and before/after SQLite hashes. Hardware and Ollama changes can alter selection quality or timing. The twelve golden cases are a narrow first milestone; broader adversarial tests and semantic/hybrid retrieval comparisons remain future work.

The baseline reports remain immutable historical evidence for commit `c4aaf5696976872e533672c1fb66b93ca3c6a4e0`; they do not certify new hardening changes with real models. [Golden review](golden-review.md), [separate holdout labels and protocol](holdout-review.md), and [offline hardening findings](../reports/adversarial-hardening.md) describe the next milestone. New live inference requires explicit owner approval of labels and budget.
