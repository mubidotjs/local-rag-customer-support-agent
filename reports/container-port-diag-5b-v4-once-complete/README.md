# V4 fictional network diagnostic: DIAGNOSTIC_COMPLETE

One authorized execution of `container-port-diag-5b-v4-once` at
`24707cacf6e55a0d6a9f5e8bd04b6af68cfa7757`. Frozen v4 plan SHA-256:
`ced9a01a7cc79553244fc2c4f69742cee4d54e13f3e391898ab580d6183f9129`. Milestone 5B runtime acceptance is not complete.

Ubuntu x86-64 ext4 preflight passed as UID/GID 10001:10001, with Landlock
ABI 7. Default context, fixed Unix endpoint, pinned Engine, image ID and
image labels matched. CLI launches required Landlock; there was no
unconfined fallback. Both CLI directories were mode 0700; `cli-config`
remained empty and only the expected Desktop log file (mode 0600) appeared
under `cli-home`. No transport drift or uncertainty was reported. This is
not strict daemon attestation.

| Check | Result |
| --- | --- |
| Normal / cleanup / total slots | 14 / 6 / 20 |
| Confirmed CLI launches | 20; exactly 3 pre-claim metadata checks |
| HTTP attempts | 2 |
| Requested mapping | `127.0.0.1:45173 -> 18080/tcp` |
| Effective mapping | `"18080/tcp": []`; no host publication |
| Internal HTTP | 200, exact fictional body |
| Ubuntu localhost TCP HTTP | Skipped: no effective mapping |
| Ubuntu private Unix-socket HTTP | 200, exact fictional body |
| Sentinel and cross-bridge TCP | Sentinel self-check passed; cross-bridge connection blocked |
| Cleanup | Six exact-ID commands succeeded; no unresolved IDs |
| Model calls, image pulls, builds, retries | 0 each |

Both bridges were internal, without endpoint gateways or IPv6. Containers
ran as UID/GID 10001:10001, with read-only root filesystems, all capabilities
dropped, no-new-privileges, bounded resources, and no restart policy. Only
the responder had the private ingress bind mount. Windows localhost was
not tested. The denial test applies to one synthetic target. Cleanup is
supported by the frozen receipts; no post-run Docker query was performed.

[Claim](claim.json) and [intent](intent.json) are byte-identical copies.
[Summary](summary.json), [journal index](journal-index.jsonl), and
[checksums](checksums.json) are separate from the raw receipts. Both private
journals remain mode 0400 under the fixed Ubuntu paths:

- Audit: `/var/tmp/project5-port-diag-5b-v4-once.preflight/actions.jsonl`,
  SHA-256 `7ba6557e4d35e63ea4bc4fb7114b4f41d07f38abba36f4ee0b8b8898af2fcd8c`.
- Execution: `/var/tmp/project5-port-diag-5b-v4-once/actions.jsonl`,
  SHA-256 `c88242f132287f2dc869b1b6ab22bb3d8b2c37d37ca33e6351d59470214618c0`.
- Permanent claim SHA-256: `56ae459b854b63de3ceada1b91de4a6a77f96b634ac2beff2e165ff488de43a4`.

The audit is the exact hashed prefix of the execution journal. The claim
and audit remain consumed. All 397 protected files, 31 original 5B archive
checksums, five published v3 checksums, three raw v3 audit hashes, and
all frozen plan digests matched before and after execution. The original
5B claim remains unchanged.

Next, separately review an offline ingress correction that keeps the
application on its internal bridge and uses a private HTTP Unix socket
with an unprivileged host IPv4-loopback proxy. V4 verifies the synthetic
Unix-socket transport, not the proposed proxy or the real application.
This archive authorizes no further runtime execution.
