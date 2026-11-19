# Private ingress executable acceptance readiness

**Offline only.** The original [proposal](../reports/private-ingress-preparation/plan.json)
is unchanged. The executable [v2 plan](../reports/private-ingress-preparation/plan-executable-v2.json)
uses the same unused ID `container-runtime-5b-private-uds-v1-once`.
Its SHA-256 is `cfdc9c141a54d04c42d16239d63eed28437cd600d09b0c66ec79358ead72a4ed`. The fictional fixture digest remains
`fd84215ee35069fc1dd9fa86352881da252e3bc358e3599d9deaa66ba3b50cde`.
Neither plan is a live authorization.

The coordinator enforces exact SHA and image IDs, a source tree digest per
role, pre-claim read-only metadata, a durable exclusive claim, and pre-send
receipts. It uses the v4 pinned Docker binary, explicit Unix endpoint,
separate CLI HOME/config and Linux Landlock; no plugin or Docker Compose
invocation is used. It derives the fixed direct CLI options from the reviewed
configuration. It caps 600 seconds, 26 Docker slots (4 metadata, 17 normal,
5 cleanup), one backend restart, one proxy launch, and 10 HTTP attempts.
No retries, pulls, Windows probes, or model calls are allowed.

## Compatibility findings

- The current Ubuntu account map assigns **UID 101 to `landscape`**. It may
  therefore exercise owner permissions on an ingress directory mapped to that
  UID. The live gate refuses this host. Do not reassign, disable, or borrow
  that system account. Review a genuinely isolated candidate with UID 101
  unused, or prepare a separately versioned UID/configuration change. If the
  candidate Engine or CLI pin differs, re-freeze its plan separately.
- The Nginx staging directory must inherit the parent group and SGID under
  umask 077. The supervisor now uses plain mkdir and verifies mode 2700,
  avoiding an explicit-mode path that can call chmod across groups. The
  socket is never published until the mode, ownership and single link agree.
- The v1 Compose rendering command would execute a separate unpinned plugin.
  V2 instead checks the tracked JSON Compose contract and exact bytes,
  reducing metadata from 5 to 4 and the total CLI ceiling from 27 to 26.
- The coordinator cannot signal a different UID or traverse the ingress
  group directly. It now uses distinct setgid handoff directories, a
  nonce tied to the SHA and socket inode, and an owner-run proxy companion
  with the same absolute deadline. The companion attests its actual IPv4
  listener and checks socket cleanup after the frontend stops.

The existing proxy rejects unapproved Host/Origin, forwarding and ambiguous
body headers, pins the socket inode and Linux peer, caps the request and
response sizes, and propagates shutdown without changing the backend.
The execution path verifies frontend and backend container mounts and no
published Docker ports; HTTP checks distinguish 200 liveness from 503
`model_disabled`. Model-disabled readiness is expected, not a failed boot.
The native SQLite fixture uses two fictional documents and deterministic
vectors, verifies FTS and source offsets, and restores to a separate file
only after stopped-state receipts. No real Nginx image/mount or application
startup was exercised in this task. The Desktop upstream-continuity
limitation from the v4 design still applies.

## Separate image-build proposal

**Proposed only:** approve a specific isolated, disconnected linux/amd64
builder with complete local base manifests/layers and the already trusted
dependency/build caches for every pinned Dockerfile step. Build from a clean
exact coordinator SHA in a trusted checkout. Both role-specific image
input hashes are computed by the read-only review command:

    python -m evaluation.private_ingress_runtime --review-inputs <exact-sha>

The frontend uses `containers/private-ingress.Dockerfile` and its
Dockerfile-specific allowlist; the backend uses unchanged
`containers/backend.Dockerfile` and `.dockerignore`. Preserve the two
pinned recipes, compare the tracked trees and the v2 input hashes, and
set all three labels for each role:

    org.project5.source=<exact-sha>
    org.project5.ingress-role=<frontend-or-backend>
    org.project5.input=<matching-review-inputs-sha256>

After separate approval, use the exact recipe with linux/amd64,
`--pull=false`, and build-step `--network=none` on that disconnected
builder. The no-network flag alone is not a registry control for the
builder; verify there is no remote resolver/egress. Missing base layers,
dependency cache, or unsupported offline resolution means STOP, with no
pull, fetch, substitution, npm registry request, or model download. Check
full immutable image IDs, Linux amd64 platform, labels, environment,
health checks, absence of volumes, users `101:101` and `10001:10001`, and
entrypoints `/usr/local/bin/ingress-entrypoint` and
`python -m support_agent.production`. Record and transfer the exact
verified offline artifacts only under separately approved preparation;
tags are insufficient. **No images were built or inspected in this task.**

## Separate host-provisioning proposal

Stop on any existing collision. In a separately approved candidate, use
read-only `getent` and `id` checks first: no host service uses UID 101;
UID/GID 10001 belong to the documented acceptance operator, whose only
extra group is the trusted Docker group; names and numeric GID 11017 and
UID 11018 are free or belong exactly to the dedicated ingress identities.
Only then, with separate owner approval, provision group
`project5-ingress` GID 11017 and account `project5-ingress-proxy` UID 11018,
primary GID 11017, nologin, no Docker or extra groups, and no privileged
permissions. Do not change existing identities. Privileged setup must be
performed by the owner; neither runtime tool elevates privileges.

| Path | Owner/mode |
| --- | --- |
| `/var/lib/project5-ingress` | root:root 0755; native ext4, trusted parents |
| `/var/lib/project5-ingress/acceptance-5b-uds-v1-once` | 101:11017, 2710, empty |
| `/var/lib/project5-ingress/acceptance-5b-uds-v1-once-control` | root:root 0755 |
| `...-control/operator` | 10001:11017, 2750, empty |
| `...-control/proxy` | 11018:10001, 2750, empty |

Use exact-path owner provisioning (e.g. install directories with explicit
numeric owner/group/mode), after checking each prefix with lstat/findmnt.
No symlinks, untrusted parents, nested mounts or unrelated paths. Directory
setgid supplies the reader group on each 0640 marker without adding the
writer to that group. Do not provision the fixed v2 audit or application
workspace under /var/tmp; atomic runtime acquisition requires both absent.
The production socket directory is `/var/lib/project5-ingress/local`;
it is **not** an acceptance source and must remain untouched.

On a candidate otherwise ready, the documented read-only gate is
`python -m evaluation.private_ingress_runtime --check-host <exact-sha>`
as operator. It sends no Docker commands, validates UID/mounts, Linux
Landlock ABI >=3, CLI file, pinned filesystem endpoint, exact SHA and
prior evidence. Provision a separate trusted read-only checkout accessible
by the proxy account. The actual Engine and preloaded images are verified
once through the future authorized runner, not by extra preflight probes.

## Future authorization and evidence

Only after the host collision is resolved and both exact image IDs are
verified, ask for one explicitly authorized run bound to this SHA, v2
plan digest, fixture digest, pinned Engine/CLI/image IDs and exact paths.
The main process would receive `--execute-authorized-once <exact-sha>`,
`--plan-sha256 <v2-digest>`, `--frontend-image <sha256:id>` and
`--backend-image <sha256:id>`. After the fixed start message the owner
would launch `python -m evaluation.private_ingress_proxy
--coordinate-authorized-once <exact-sha> --plan-sha256 <v2-digest>`
once as 11018:11017. Both processes independently check identity. No
owner privilege switch occurs inside either runner. The handoff consumes
only its own fresh markers, never uses an old readiness receipt, and
expires at the shared deadline.

On deterministic disproof, close the proxy and clean up only exact
owned IDs within the budget. On lost/ambiguous dispatch, identity
drift or timeout, stop Docker operations and retain journals,
claim and remaining IDs. The companion stops at its deadline; no
implicit reconnection or cleanup is permitted. Windows browser access
needs separate authorization. This is executable preparation only,
not runtime acceptance.
