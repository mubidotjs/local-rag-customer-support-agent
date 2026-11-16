# Local document lifecycle

Milestone 3 is verified with invented documents, temporary SQLite databases and deterministic fake vectors only. No real model, model metadata, model download or live reindex acceptance was run. Historical evaluation authorization remains consumed.

## CLI workflow

The original `python -m support_agent.cli corpus` invocation is preserved. It creates an initial index, treats an identical valid corpus as unchanged, and still rejects a changed populated corpus. It does not synchronize or delete documents. New administrative commands are CLI-only; there is no mutation HTTP endpoint.

For an **invented offline sandbox**, put valid fictional Markdown/text documents in `.local/invented-docs` (metadata format matches `corpus/`). Use a fresh database:

```powershell
python -m support_agent.cli .local/invented-docs --db .local/lifecycle-demo.sqlite3 --synthetic
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 status
```

Synthetic vectors are explicitly bound to a fake digest and cannot be mixed with real vectors or queried through the real adapter. They demonstrate storage contracts, not retrieval quality or AI acceptance. These examples never select historical databases.

Preview an addition, inspect its JSON, then explicitly confirm that exact proposal:

```powershell
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 preview add --directory .local/invented-docs --file new-guide.md --plan .local/add-plan.json --synthetic
$proposal = Get-Content .local/add-plan.json -Raw | ConvertFrom-Json
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 apply --plan .local/add-plan.json --confirm $proposal.confirmation --synthetic
```

Other preview operations (apply with the same confirmation workflow):

```powershell
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 preview update --id new-guide --directory .local/invented-docs --file new-guide.md --plan .local/update-plan.json --synthetic
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 preview delete --id new-guide --plan .local/delete-plan.json
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 preview reindex --plan .local/reindex-plan.json --synthetic
```

`--id` is the stable metadata document ID. Updating may change title, version, topic or filename, but must preserve that ID. Delete removes only the selected stored document/chunks/FTS entries; it does not remove any source file. Reindex uses **stored sources**, never a directory listing. A missing file cannot cause deletion; an unselected file cannot cause addition. No synchronization mode is implemented.

Plans are bounded JSON, created exclusively without overwriting an existing file. They pin the database path/file identity and logical state hash, source hash, selected operation, model digest, preprocessing fingerprint and resource settings. The preview shows old/new versions, document/chunk counts, vector reuse/regeneration counts, changed chunk IDs/hashes/offsets and removed chunk IDs. Review the selected local source together with that preview. `apply` requires the full confirmation token, including for additions. It never prompts for a generic yes or silently applies a different plan. Tokens are review checks, not authentication against the local owner.

Reusing a consumed change plan fails as stale. Previewing the now-identical update or compatible reindex produces a no-op with zero calls and no database write. Regenerate a preview after any source/index/configuration change. A failed apply is not automatically retried; inspect the failure and explicitly rerun only when appropriate.

## Vector identity and compatibility

The schema remains version 1. Existing `metadata` key/value storage supports three optional fields: `embed_digest`, `embed_config`, `index_revision`. No table migration or implicit baseline rewrite is necessary. Legacy four-key indexes remain readable with the original adapter. Older application releases reject the additional managed metadata; backward compatibility here means the new release still reads old indexes and preserves the original CLI workflow. The first lifecycle add/update against an unbound legacy index is rejected; an explicit previewed/confirmed full reindex establishes a binding and **regenerates all vectors**. A tag alone cannot prove which model created legacy vectors.

Reuse requires identical embedding model name, digest and preprocessing fingerprint. The fingerprint covers the versioned normalizer/chunker, chunk size, prefixes, context setting and truncation policy. Changing any of these requires an explicit full reindex and disables reuse. Normalizer/chunker behavior changes must bump their fingerprint versions in code.

Reuse compares exact normalized chunk text within the same document. The same ID/text keeps its own stored vector JSON byte-for-byte, including duplicate chunks. Remaining chunks map one-to-one to the lowest-ID unused old identical text, allowing shifted chunks to retain vectors. An extra newly duplicated chunk still requires a new vector. New/changed text is embedded in batches of at most 16. Every vector must have finite, nonzero values, a valid dimension and bounded encoding; dimensions cannot change during an incremental operation. No similarity-based reuse occurs.

Unchanged chunk IDs and offsets stay intact. IDs retain the original `<document-id>:cNNN` ordinal convention; inserting text before a chunk may change its ordinal/ID and offsets. The mapping is recomputed from the complete new source, even when the exact text permits vector reuse. Metadata-only and CRLF/BOM changes can preserve vectors while changing raw or normalized offsets. Citations include versions and both offset systems; consumers must not treat a chunk ID alone as a globally immutable content identifier.

For future owner-authorized real use, `ManagedOllama` implements the same injected binding/embed interface. Construction, preview and status make no network request. Each explicit embed verifies the installed pinned tag via a bounded `/api/tags` GET before and after one `/api/embed` POST, checks the response model name, and never downloads/substitutes/retries. Digest: `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f`. The daemon and local owner are trusted not to retag models concurrently; these checks cannot authenticate a hostile daemon.

The lifecycle CLI requires `--allow-local-embedding` to select that adapter when new vectors are required. **This milestone does not authorize executing that flag.** A new real-bound index requires `RAG_MANAGED_EMBEDDINGS=1` in the API process so queries use the same verified adapter. An unidentified or mismatched query adapter fails before a query embedding. Legacy querying remains unchanged. The generation model remains the existing qwen3:4b; no new generation evaluation was performed.

## Atomicity and recovery

Application revalidates the current index/source before embedding, prepares vectors outside the writer transaction, then obtains `BEGIN IMMEDIATE`. It rechecks the index hash, file identity, source and proposal under that lock before touching rows. Changed/removed chunks and FTS entries are replaced together, documents retain stable IDs, the revision increments once, and final integrity/FTS5/foreign-key/page-budget checks precede COMMIT. Exceptions and failed commits roll back. Unchanged rows/vectors remain intact. A stale concurrent writer cannot overwrite a newer committed generation, though already completed embedding work may be discarded.

Read connections remain `mode=ro`, `query_only=ON`, `trusted_schema=OFF`, and now use an explicit read transaction across all integrity/retrieval SELECTs. A concurrent reader sees its complete old generation until it closes. A writer may prepare work but must wait for readers to commit under DELETE journaling; lock waits are bounded by SQLite's five-second connection timeout. A timeout fails and rolls back, with no automatic retry. New readers can see the new generation after commit.

Lifecycle operations require DELETE journal mode and reject WAL or other journal modes. No automatic mode conversion or WAL checkpointing is performed. This bounds managed sidecar behavior and keeps concurrency/recovery assumptions explicit.

SQLite's rollback journal handles an interrupted process. A read-only connection may be unable to recover a hot journal and will fail closed. The local owner can explicitly run:

```powershell
python -m support_agent.cli documents --db .local/lifecycle-demo.sqlite3 recover
```

This opens a bounded writer lock so SQLite can perform its own journal recovery, then validates source/chunk/vector/FTS integrity. It never reconstructs rows, rebuilds FTS, changes labels or regenerates vectors. Do not manually delete journal files. Back up a closed database before maintenance; this milestone does not add snapshots, undo history or recovery from media corruption. Recovery cannot reconstruct a lost committed index.

Files must be direct regular UTF-8 Markdown/text files in an explicitly selected local directory; symlinks, junctions, hardlinks, traversal, URL filenames, duplicate IDs and case-insensitive filename collisions are rejected. Source reads are capped and file identity/size/timestamps are checked around reading. Indexes/sidecars also reject links. Arbitrary concurrent filesystem replacement by a hostile owner is outside the trust model; SQLite coordination applies to cooperating writers. Sources are copied as inert data, never executed. Quarantine, ambiguity, exact source sentences, relevance proofs and all existing request/context/model limits remain enforced.

## Offline acceptance and remaining limits

Run `python -m pytest -q` for lifecycle, regression, atomicity, crash-recovery, read-only and security checks. `python -m evaluation.lifecycle_offline` replays a small synthetic lifecycle in a fresh temporary directory and prints reproducible counts. Its optional `--output` path is exclusive-create. It never contacts Ollama. The [milestone report](../reports/document-lifecycle/report.md) separates these results from historical model acceptance.

No directory synchronization, multi-user administration, automatic retry, persistent preview queue, document history/undo, source editing UI, cloud service, deployment or orchestration is added. Real embedding/reindex acceptance is still unverified; the separate [proposed budget](../reports/document-lifecycle/proposed-live-budget.json) is not authorization to execute it.
