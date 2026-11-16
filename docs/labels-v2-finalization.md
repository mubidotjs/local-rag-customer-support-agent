# Finalized corrected labels v2

The owner explicitly approved `review-v2-proposed` at commit `620d970c01232e28a73b72e37385b28c6d90203a` **for label finalization only**. The separate [immutable v2 package](../evaluation/labels/v2/manifest.json) records that approval without rewriting the original labels, proposal, reports or hash manifests.

The label digest is SHA-256 of the exact UTF-8 bytes of [labels.json](../evaluation/labels/v2/labels.json), using LF line endings and a final newline:

```text
b7a4600f21bf2e030390ecb7b749c5c2d9ac6feb9fc826250de7665baa1a22e6
```

This digest covers both corrected datasets, the required reset source locations, and the preserved false-abstention finding. [labels.sha256](../evaluation/labels/v2/labels.sha256) provides the standard checksum entry. The [manifest](../evaluation/labels/v2/manifest.json) also pins the approval record and still-unauthorized budget. Offline regression tests pin both the label and manifest digests. These published version files are append-only: future approved corrections must receive a new version and digest rather than overwrite v2.

## Exact approved content

- Golden `reset` requires both exact sentences: the 10-second holding instruction and “Release the reset button when the status light flashes amber three times.” Supporting document `reset`, version `2.3`, chunk `reset:c000`, and original normalized/source offsets `[99,202)` and `[203,276)` are preserved in the package.
- Holdout `lexical-paraphrase` remains `supported`, `abstain=false`, with its original evidence unchanged. The existing historical synthetic false abstention remains visible; the 11/12 scripted finding was not relabeled, rescored, or replaced by a new model claim.
- Holdout `document-injection` retains abstention and contextual source evidence. Its classification is `security-quarantine`, its rationale specifies document-wide security quarantine rather than factual conflict, and `expected_verification_reason` is `untrusted_source`.
- All other case labels and validator probes match the approved candidate exactly. The candidate's historical holdout `label_status` string is omitted from the final payload because it describes a draft, not a case outcome. Its schema and baseline provenance remain intact. Approval scope is recorded separately; independent human review of every case is not claimed.

## Approval and preservation

[approval.json](../evaluation/labels/v2/approval.json) records the explicit owner instruction, exact approved proposal commit and file hash, limited approval scope, and the resulting label digest. It is a record of the chat instruction, not a cryptographic owner signature.

All 54 protected original local artifacts matched the [unchanged preservation snapshot](../evaluation/proposals/review-v2-preservation.json) before and after label finalization. This includes original golden/holdout labels, original hash manifests, all corpus files, verification reports, the baseline SQLite index, and both original model-backed browser screenshots. CI checks the separately pinned original Git representation where local CRLF and Git LF bytes differ. The original baseline and holdout evaluators retain their existing defaults; no historical result is regraded against v2.

Offline validation passed: **74 pytest tests** (one real-model test skipped), **3 Vitest tests**, **4 synthetic desktop/mobile Playwright tests** (two real-model project variants skipped), and the TypeScript/Vite build. New regressions verify exact agreement with the approved proposal, only the approved label differences, reset source offsets, the retained false-abstention finding, immutable checksums, all protected original hashes, and label-only authorization. The exact published commit's GitHub Actions result is reported separately; CI has no Ollama dependency.

## Phase D remains unauthorized

The [version-bound budget](../evaluation/labels/v2/execution-budget.json) references this approved label digest but remains **unapproved for execution**. Its ceiling is unchanged: **12 generation POSTs, 23 embedding POSTs (11 ingestion batches + 12 queries), 20 minutes total, one attempt per question, zero retries, extra smoke calls, downloads or substitutions**.

Pinned existing model identities remain:

| Model | Required existing digest |
| --- | --- |
| `nomic-embed-text:latest` | `0a109f422b47e3a30ba2b10eca18548e944e8a23073ee3f3e947efcf3c45e59f` |
| `qwen3:4b` | `359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7` |

No embeddings, generation, live evaluation, model metadata requests or downloads were performed. The index was not opened for queries or writes during label finalization; preservation used read-only file hashes. Offline tests use explicit synthetic fixtures and temporary databases. There is no new live runner, execution permission, application behavior change, or cloud deployment in this task. Separate explicit owner authorization is required before Phase D.
