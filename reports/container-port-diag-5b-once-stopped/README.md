# Bounded CLI v3 network diagnostic: stopped before claim

The one authorized execution at `21949c13b7db31166dd86e7cf697d78908deb628` stopped during the image
preflight step. Its exclusive preflight audit is retained and this diagnostic ID
cannot be reused. No permanent execution claim or workspace was created.

The default context, fixed Unix endpoint, and pinned Engine identity were
verified. The private config directory remained owned by 10001:10001 with mode
0700, but was no longer empty after the second CLI invocation. It contained the
Docker Desktop log at `.docker/desktop/log/host/docker-desktop.log`. The runner
sets both `HOME` and `--config` to that directory. Its emptiness guard rejected
the image step before creating a CLI subprocess, returning
`docker_config_not_private_empty`. This is a runner layout issue, not evidence
of a mismatched image, changed Engine, or ingress failure.

| Count | Actual |
|---|---:|
| Normal action slots journaled and charged | 3 |
| Docker CLI invocations | 2 |
| Metadata responses | 2 |
| Cleanup commands, HTTP attempts, or model requests | 0 each |

No mutation was attempted, no IDs were allocated, and no cleanup was needed.
Requested/effective port mappings, internal HTTP, Ubuntu localhost, Unix-socket
HTTP, sentinel, and cross-bridge isolation were not measured.

[Intent](intent.json) and [result](result.json) are byte-identical copies of the
local audit. The [journal index](journal-index.jsonl) records every event without
exposing the complete Engine inventory. The original mode-0400 journal remains
at `/var/tmp/project5-port-diag-5b-once.preflight/actions.jsonl` with SHA-256
`d62fd2324204818a130ff5471bd429ec83979bd2a3d6a2d3e2cdc7434a19465f`.
[Summary](summary.json) and [checksums](checksums.json) distinguish original
and published evidence. The historical 397-file inventory, all 31 consumed-run
archive checksums, and all three plan hashes passed before and after execution.
The original 5B claim remains unchanged.

For separately authorized future work, decouple Docker's private `HOME` from
its empty `--config` directory. Keep both under an isolated owner-only path,
preserve the strict configuration guard, and add a synthetic regression in
which the CLI creates the observed log tree under HOME. Track reserved actions
and actual CLI launches separately. This stopped diagnostic remains consumed;
do not edit its audit or attempt a rerun. A new diagnostic plan and explicit
authorization would be needed. The delivery commit only preserves evidence.
