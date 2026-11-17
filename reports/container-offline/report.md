# Milestone 5A offline packaging report

Predecessor: `d5c7cfad14c2ce1499a7e6459ddea6f464ac6332`.
Exact source SHA is the delivery commit containing this report. Required checks
are the original `CI`, `Offline investigation`, and new `Offline packaging`
workflows on that same SHA. Final delivery reports their exact run links.

**Offline checks PASS. Local container runtime acceptance PENDING 5B.**
Docker CLI 29.9.2 is present; the `desktop-linux` Engine pipe was unavailable.
No Docker containers, Ollama services or relay services were started. CI builds
images and records image metadata; build success is distinct from runtime success.
Builds also run Nginx's syntax check and import the production backend against its
minimal runtime dependencies, asserting optional SDKs are absent. They do not
start a server or contact a model.
Actual embedding, generation, Ollama metadata and model-download requests: **0**.
No historical one-shot claim was reset/reused and no new live claim was consumed.

## Packaging and operational changes

All delivery files are new. No predecessor tracked source, dependency lock,
workflow, corpus, index, label, report, claim or browser/model artifact changed.

- Multi-stage backend/frontend builds with immutable Linux-amd64 Python, Node and
  unprivileged Nginx manifests. The 17 runtime Python wheels use existing version
  pins plus SHA-256 hashes; npm uses the unchanged integrity lock. Runtime excludes
  Node/npm, pytest, pip headers/tools and optional SDKs. No model weights/data enter
  the allowlisted image context.
- Same-origin UI/API. Only frontend `127.0.0.1:5173` is published. Backend binds
  namespace loopback; the pair use an internal network, non-root users, read-only
  roots, dropped capabilities, no-new-privileges, bounded tmpfs, CPU/memory/PIDs,
  finite restart count and graceful stop limits. No Docker socket or privileged
  service exists.
- Explicit pre-existing private data bind mount, read-only during serving. Fixed
  non-root UID/GID, 0700 directory, 0600 regular single-link index, no symlink path,
  inode pinning and DELETE-journal checks. Missing/corrupt/incompatible index,
  sidecar recovery or replaced mounts fail closed. No automatic ingestion/recovery.
- Separate production entry point preserves the original `/api/ask` and five MCP
  tool sources. Adds request body/header/deadline/admission limits, liveness and
  read-only structural readiness, request IDs and redacted structured request logs.
  One-inference lock, origin/host rules and exact grounding remain inherited.
- Model access disabled by default. An explicit, owner-started native Linux Unix
  socket relay is the only container model route. It verifies peer UID and finite
  allowlisted HTTP operations; its upstream is fixed host loopback, without proxy,
  redirect, arbitrary URL, tool, remote-model or download capability. Adapter model
  identities/prefixes/configuration are pinned. Health probes never contact it.
- Explicit network-disabled maintenance role delegates to the old CLI, fixing the
  database and setting umask 077. Real-embedding flags are rejected. New stopped-
  writer backup/confirmed restore CLI validates bounded copies, hashes, private
  paths and SQLite, fsyncs staged files and replaces atomically. No administrative
  HTTP or MCP write capability was added.

[Operations, architecture diagram, platform requirements and recovery](../../docs/containers.md)
and [threat/permission matrix](../../docs/container-threat-model.md) describe the
boundaries and commands. No live operating command was executed in this milestone.

## Verification

| Check | Local Windows result |
| --- | --- |
| Full optional-stack pytest | 444 passed, 5 skipped |
| Base environment without optional SDKs | 340 passed, 40 skipped |
| New focused cases | 57 passed, 4 native Linux checks skipped |
| Vitest / production frontend build | 4 passed / PASS |
| Synthetic Playwright | 4 passed, 2 real-model variants skipped |
| Base and UDS Compose static rendering | PASS without Docker Engine |
| Guarded full-suite actual model connection attempts | 0 |
| Strict local protected hashes | 367/367, none missing |
| New-code local statement / branch coverage | 89.98% / 71.56%; combined 84.53% (four Linux skips) |
| Local image build / container execution | NOT RUN: Engine unavailable |

The new CI additionally runs POSIX ownership/backup/restore, actual Unix peer
credentials, and a native non-container Uvicorn SIGTERM fixture. These native
fixtures use invented data and disabled/fake model routes. Image builds install
public package/base-image dependencies but never launch services or models.
Build inputs are pinned; byte-identical OCI archives across builders/timestamps
and vulnerability-free images are not claimed.

[coverage.json](coverage.json) gives statement/branch totals and uncovered paths
for production code and the preservation/static checker. Local coverage includes
four Windows skips; Linux CI measures and uploads its own full coverage JSON with
an 80% combined gate. No missing-path coverage is hidden. Coverage does not prove
complete security.

[network-proof.json](network-proof.json) records zero attempts at the known Ollama
TCP port/Unix socket in the guarded full Python test process. CI fails even if a
test catches a blocked connection exception. The native server child explicitly
uses disabled mode; image build steps have no Ollama/model runner. This is scoped
proof, not an OS-wide network monitor or air-gap claim. No real-inference tests
or browser-to-model variants were enabled.

Tests cover unsafe settings, model/metadata mismatch, source/index corruption,
replacement, mount permissions/links, read-only rejection, bounded bodies and
headers, slow/disconnected bodies, concurrency, source-backed fake answers,
relay peer/authentication failure, HTTP smuggling/oversize/recursive JSON, fixed
loopback forwarding, cancelled relay startup/shutdown, safe backup/restore and
CLI confirmation. Existing optional-SDK absence, citation, relevance, injection,
conflict and lifecycle suites still pass.

## Preservation

The [367-file inventory](preservation.json), including all predecessor tracked
files and available historical ignored files, has SHA-256:

`4892195abd9567d09543dfec7d0c0148f0a0dce8dec240565eca17d8a8c8cafb`

It includes all 309 previously protected files plus the full consumed 4B delivery,
claim, isolated index/sources and execution proof/log. Local verification requires
every file. Fresh CI may omit only explicitly enumerated ignored files. The
original 4B plan, original/delivery artifact manifests and consumed claim are
unchanged. Historical absent failed-run database remains absent. Projects 1–4,
the separate Production AI Knowledge Agent and existing deployments were not
accessed or modified.

[offline.json](offline.json) records local results, new source hashes, base-image
manifests and explicit model/runtime status. CI image/config/coverage/network
artifacts are attached to the exact source run.

## Findings, blockers and next scope

The new production boundary addresses missing body receive deadlines, container
filesystem/permission ambiguity, raw request log exposure and unsafe assumptions
about container-to-host loopback. It preserves the development entry point instead
of rewriting historical application behavior.

Remaining limits:

- Local Docker Engine is unavailable, so no container runtime PASS is claimed.
- Native Windows Ollama through Docker Desktop, socket translation, non-default
  UID mappings/rootless Docker and ARM are unsupported in this initial package.
  The existing bare application remains usable; no host-network bypass is offered.
- Readiness checks source/storage/route structure, not live daemon/model health.
  Actual authorized requests validate identities and fail closed on unavailable
  services. No model requests are hidden in probes.
- External Ollama resources are not governed by container CPU/memory limits.
  Cancellation is cooperative; a client timeout does not prove daemon compute
  stopped. Relay upstream I/O is finite and never retried automatically.
- Stopped-writer backup/restore assumes a cooperative owner. Existing finite
  grounding, source-authority and semantic-correctness limitations are unchanged.
- Optional LangGraph/MCP SDKs are intentionally absent from the minimal runtime;
  the original host optional environment and five read-only permissions remain
  unchanged, with clear failure when SDKs are unavailable.

Proposed separately authorized **5B**: one new fictional Linux Docker workspace,
one bounded start/restart/stop sequence within ten minutes; verify user identities,
limits, same-origin API, volume persistence, read-only failure, health degradation,
SIGTERM/recovery and a fake private UDS endpoint. **Zero real Ollama metadata,
embedding, generation or downloads.** Do not interfere with the real loopback
daemon. A genuine model test would require another explicit plan/budget. No 5B
runner, execution, deployment or 5C work is included here.
