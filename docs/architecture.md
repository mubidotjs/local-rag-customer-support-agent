# Architecture and trust boundaries

```text
CLI -> validate local files -> normalize + source map -> deterministic chunks
    -> fixed loopback Ollama /api/embed -> atomic SQLite documents/chunks/FTS5/metadata

React -> POST /api/ask -> read-only SQLite + fixed loopback query embedding
      -> cosine + FTS5 BM25 -> RRF -> relevance/ambiguity gates
      -> one local qwen3:4b selection -> exact quote/citation validator -> answer or abstention
```

## Retrieval algorithm

The query is at most 600 characters. Lowercase ASCII alphanumeric query tokens, remove common English function words and fictional product names, deduplicate, sort, and keep the first 32. Feed quoted tokens joined by `OR` to FTS5 through a bound SQL parameter; user FTS operators never execute. Sort BM25 ascending, then chunk ID; retain 20.

Normalize query and stored vectors, verify dimensions against the index metadata, take their dot product, and sort descending, then chunk ID; retain 20. Iterate their union, assign equal-weight reciprocal rank fusion with constant 60, and sort by descending fused score, ascending ID. Select the first six chunks fitting the 4,800-character budget. Scores and both contributing ranks are visible in the UI. SQLite's `unicode61` tokenizer may split words differently from the deliberately simple query tokenizer.

For generation, require cosine >= 0.40 and at least one substantive query token present in a passage. This is a conservative, explicit gate, not a calibrated probability. If the highest-ranked eligible passage's topic has multiple active document IDs, abstain as ambiguous. Every final citation to any ambiguous topic is also rejected, even if it was not the highest-ranked passage. The corpus deliberately includes a conflicting warranty pair. There is no automatic conflict resolution or latest-version guessing.

## Grounding contract

The local model returns JSON containing `abstain` and at most three `{chunk_id, quote}` claims. Runtime schema enums contain only eligible retrieved IDs and complete evidence sentences. This follows Ollama's [structured-output API](https://github.com/ollama/ollama/blob/main/docs/api.md). The server does not trust schema compliance: it checks types, keys, IDs, entire-sentence equality, duplication, lexical overlap, cosine floor, and ambiguous topics independently. A single invalid claim rejects the entire selection. The server builds the answer itself using only verified quotes; generated commentary is never exposed.

Question and documents are JSON-encoded untrusted data in a separate user message. The system instruction prohibits following embedded commands, outside knowledge, or tools. No tools are offered, and no model output is executed. React renders all text without `dangerouslySetInnerHTML`. These controls confine capabilities; they are not a complete prompt-injection detector. A malicious document can still contain false factual statements. Corpus selection and human maintenance remain the local owner's responsibility.

## Bounds

| Resource | Bound |
| --- | --- |
| Per-document UTF-8 bytes | 64,000, enforced with a capped read |
| Stored documents / chunks | 50 / 500 |
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
