# Project 5: isolated ingress diagnostic

The stopped 5B claim remains consumed. This preparation does not change Compose,
the application, the original runner, or any of its evidence.

## Finding and recommended correction

The [archived inspect receipt](../reports/container-runtime-5b-once-consumed/raw/action-06.json)
records a requested `127.0.0.1:5173 -> 8080/tcp` binding in
`HostConfig.PortBindings`, an empty effective `NetworkSettings.Ports` entry,
and a frontend attached only to the internal bridge with no endpoint gateway.
Nginx's internal health check passed at 17:41:05.697 UTC; the Ubuntu HTTP
connection was refused at 17:41:09.156 UTC. An inspect entry describes Docker's
publication state, and even a populated entry would not prove reachability
from Ubuntu or Windows. This evidence favors a network publishing incompatibility;
it does not establish a Windows forwarding fault or an application listener race.
The old runner trusted the requested mapping, which is a separate verification gap.

[Docker's internal network contract](https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode)
omits a default route. A [Moby maintainer explains the port-selection rule](https://github.com/moby/moby/discussions/53256):
publication uses the default-gateway network. [Docker Desktop](https://docs.docker.com/desktop/features/networking/#how-exposed-ports-work)
adds a VM/host forwarding layer. [WSL NAT and mirrored modes](https://learn.microsoft.com/en-us/windows/wsl/networking)
add another host-specific distinction. Neither layer can be verified by Linux
container inspection alone.

| Design | Security and platform tradeoff |
|---|---|
| Existing internal bridge plus `--publish` | Cannot rely on a published port with no eligible gateway. Runtime gate must require the exact effective mapping **and** a bounded successful listener check before host HTTP. |
| Normal bridge or dual-home the existing frontend | Gives the shared frontend/backend namespace the new interface and routes. Uvicorn binding to namespace loopback protects its listener, but does not restrict backend outbound connections. Host loopback publication does not restrict container egress. Reject. |
| Dual-homed dedicated proxy | Could separate application and ingress namespaces if the backend shared only the internal side and a new, independently enforced deny-egress policy covered the proxy. A normal bridge alone does not enforce this; reject without proof. |
| Native Linux direct container IP (including a local TCP proxy to that IP) | An ordinary internal bridge can still expose its container IPs to the daemon host and potentially allow bridge-address host services. Linux host reachability is not a Windows Desktop contract. Do not use it as the localhost-only design. |
| Private Unix socket plus unprivileged host loopback proxy | Recommended design. Retain the internal application namespace and its backend loopback. Remove Docker port publication. Have Nginx serve HTTP on a dedicated private socket on native Linux storage and an owner-started, unprivileged proxy bind only host `127.0.0.1:5173` and forward only to that fixed socket. No additional application route or network attachment. |

The proposed production socket must be separate from the model socket and the
index. Mount only its dedicated directory in the frontend; require a trusted
private parent, fixed frontend and authorized proxy ownership/group, a validated
socket inode/type/mode, no symlinks, no world access, and no broad mount. Use
least privilege for both processes; keep backend data read-only and model mode
disabled. The proxy must preserve the existing Host/Origin checks, reject
unapproved Host/Origin or ambiguous requests, never derive an upstream from
headers, and bound concurrency, bytes and time. Never attach Docker sockets,
host networking, host-gateway overrides, public listeners or a model relay. An
internal bridge alone does not exclude daemon-host traffic; evaluate Docker's
[isolated gateway mode](https://docs.docker.com/engine/network/port-publishing/#gateway-modes)
as a separate production gate to remove the bridge address. Binding to loopback
is not local-user authentication; operators and Docker administrators remain
trusted. Service identities and the production proxy are outside this change.

Native Linux ext4 is the straightforward candidate for socket sharing.
Docker Desktop with Ubuntu WSL2 must independently prove the exact bind-mounted
socket interoperates. Do not switch to a Windows/DrvFs socket, broaden a bind,
or adjust WSL/Docker settings to force this to work. Windows localhost behavior
is explicitly out of scope for this run: it requires a separate bounded
Windows-side check and approved forwarding mechanism. If the UDS path fails
on Desktop, stop; use an independently validated native Linux host design.

## Frozen diagnostic

See the [split-home CLI v4 frozen plan](../reports/container-port-diagnostic-preparation/plan-split-cli-home-v4.json)
(SHA-256: `ced9a01a7cc79553244fc2c4f69742cee4d54e13f3e391898ab580d6183f9129`).
The [original](../reports/container-port-diagnostic-preparation/plan.json),
[preflight v2](../reports/container-port-diagnostic-preparation/plan-preclaim-v2.json),
and [bounded CLI v3](../reports/container-port-diagnostic-preparation/plan-bounded-cli-v3.json)
plans are all preserved with their original hashes. The [stopped v3
evidence](../reports/container-port-diag-5b-once-stopped/README.md) shows why a
shared HOME/config directory fails: Desktop creates its log under HOME.
The v3 ID and audit remain consumed. Version 4 uses
`container-port-diag-5b-v4-once` and new Docker resource names and labels.
It uses the already-approved local Python image by full ID and runs only inline
fictional responders; inherited health checks are disabled, proxies are neutralized,
and pulling is forbidden. Two separate internal IPv4 bridges contain one
non-root responder each; only the HTTP responder gets a dedicated private
bind mount. No historical database, application server, model service or
runtime relay is started. The TCP sentinel has no published port.

A bounded wait for the private listener inode precedes a single exact in-container
HTTP check. Missing or unsafe metadata stops before probes. The explicitly
empty/null mapping is a **negative diagnostic observation**: the strict
publication checker rejects it, so the Ubuntu TCP GET is skipped. A populated,
exact mapping permits one Ubuntu localhost GET. The independent Ubuntu
HTTP-over-UDS GET then tests private socket sharing directly. No browser-facing
proxy or Windows forwarding is attested by that GET. The sentinel self-check
and absence of external routes precede one denied TCP connection toward the
newly owned sentinel, with no DNS lookup or public target. This checks one
cross-bridge boundary, not every possible egress path.

Maximum **180 seconds, 20 Docker commands (14 normal + 6 cleanup), 3 HTTP
attempts**. The expected no-publication branch uses two HTTP attempts; the
third is conditional. The two sentinel TCP checks are not HTTP. No retries,
no extra probes, no endpoint/ID overrides. The execution gate requires the
owner-approved exact clean commit, both pinned plan digests, pinned image and
Engine, all 397 protected files, and all 31 consumed archive checksums.

### Revised preflight and claim

The claim workspace `/var/tmp/project5-port-diag-5b-v4-once` and its fixed sibling, `/var/tmp/project5-port-diag-5b-v4-once.preflight`,
must both be absent. Exclusive directory creation reserves the audit once.
The runner flushes and fsyncs each pre-send record before dispatch and saves
the confirmed child launch separately, then the result or error. The three read-only Docker commands (context, Engine,
pinned image) are the first three of the same 14 normal commands. It never
runs them again after the claim. On metadata failure or interruption, retain
the audit and refuse reuse; an incomplete directory is evidence and blocks
another invocation. A failed preflight does not create the permanent claim.

On success, recheck source and preservation, seal the audit, validate its
digest, exact ordered receipts, and locally observed endpoint identity, and exclusively create the permanent workspace.
Adopt the exact preflight bytes as the execution journal prefix and record
their count and SHA-256 in the claim. The result distinguishes reserved slots from
confirmed launches; an interrupted spawn is uncertain and is never retried. The directory reservation is the
atomic exclusion point; only a durably populated workspace authorizes
mutation. A crash during adoption leaves the audit and the consumed workspace
in place without dispatching a mutation. Seal the complete journal and record
its hash in the result. Cleanup remains bounded to captured IDs when the CLI and local endpoint still
match and there is no transport or ownership uncertainty.

### Bounded CLI risk model

Ubuntu WSL2 must present the root-owned entry /usr/bin/docker pointing to the
pinned Docker Desktop Linux ELF binary. The runner checks the binary's SHA-256
and trusted path components, then rechecks the file and socket stamps around
dispatch. It executes the verified file descriptor, not a PATH lookup. The
CLI receives only a minimal environment. Separate mode-0700 directories
under the exclusive audit path supply `HOME`/working directory (`cli-home`)
and `--config` (`cli-config`). The config directory must stay empty before and
after every invocation. Caller Docker overrides are refused. HOME may contain
only the observed log tree and a bounded regular log file; there is no
allowlist exception for config entries. Ownership, modes, directory identity,
symlinks and log hardlinks are checked. Unexpected HOME entries stop the run.

On x86_64 Linux, the CLI process must apply [Landlock](https://docs.kernel.org/userspace-api/landlock.html)
ABI 3+ to keep regular data writes, creation, removal, and truncation within
its private HOME. The CLI child sets no-new-privileges first. It cannot write
configuration or journals through the child filesystem policy. This is not
a general metadata sandbox (for example, Landlock does not mediate chmod)
or a constraint on the Docker daemon. The owned-directory guards independently
enforce permissions and identity. No host settings or privileges are changed.
If confinement is unavailable or the observed log layout changes, stop. It inspects context default
once, then directs every other command explicitly to unix:///var/run/docker.sock.
Engine ID and pinned image ID, platform, user, source/context labels, and
environment are verified before claiming. The plan counts CLI invocations;
the runner starts no hidden probes or additional CLI processes. This is not
an Engine API request count.

A matching Engine ID is a point-in-time check. An unchanged socket and matching
receipts cannot reveal a silent upstream substitution with indistinguishable
responses. The owner accepted this residual risk only for the isolated fictional
diagnostic on a trusted Desktop host. This does not attest daemon continuity.
Changes to the CLI digest, endpoint, or plans require a new review; do not
substitute an upgraded binary or alternate socket.

A deterministic bad diagnostic result with an otherwise trusted target permits
the original bounded cleanup on exact owned IDs only. A timeout, lost response,
malformed create receipt, ownership mismatch, or detectable CLI/socket drift
stops every further Docker dispatch, including cleanup. A mutation with an
unconfirmed receipt is possibly committed. Do not retry or reconnect. The
result records captured IDs still unresolved and any candidate returned before
identity drift; candidates are not trusted cleanup targets.

### Separate owner inspection after an ambiguous failure

Do not run these steps under the diagnostic budget or resume the run. Obtain
separate authorization for read-only inspection. Preserve both audit paths,
the result, and the journal hash. In the original Ubuntu environment, verify
the pinned CLI file, the local socket, and the approved Engine ID on the
fixed endpoint before inspecting any recorded full ID. Inspect container IDs
as containers and network IDs as networks. Compare each receipt's full ID,
diagnostic label, expected role name, image for containers, and topology to
the journal. If a create response was lost, use the exact diagnostic-label
filter org.project5.diagnostic=container-port-diag-5b-v4-once on the applicable
container or network list only to find candidates, then inspect their full
IDs and corroborate the role, creation time, source mount (responder only),
and topology. Stop if the Engine or ownership cannot be verified. Inspection
does not authorize removal. Any subsequent removal needs separate approval
and exact verified IDs; no name-based, force, wildcard, or prune cleanup.

### Separate live authorization

Authorize exactly one use of the bounded CLI plan at its reviewed clean
source SHA, with its exact pinned CLI digest, Engine and image IDs, Ubuntu
ext4 checkout as UID/GID 10001:10001, successful offline CI, and both v4 live
paths absent and the v3 audit untouched. Require Linux Landlock ABI 3+.
Explicitly accept the documented upstream visibility limit.
Use the existing module with --execute-authorized-once and the approved
source SHA. Keep the 180-second maximum, 14 normal Docker CLI invocations
including exactly three pre-claim metadata checks, six cleanup invocations,
at most three fixed diagnostic HTTP attempts, zero retries, and zero model
requests. This approval would cover only the fictional diagnostic.

The manual offline CI path runs fake Docker regressions and the Python
and frontend suites. It deselects the single test that invokes the actual
Compose CLI. The standard image-build workflow is intentionally not launched
for this revision. No application, Windows loopback, or model diagnostic is
part of this work.
