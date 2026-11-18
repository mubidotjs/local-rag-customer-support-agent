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

See [the frozen plan](../reports/container-port-diagnostic-preparation/plan.json).
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
owner-approved exact clean commit, pinned plan/image/engine, all 397 protected
files verified, and an absent fixed ext4 workspace. Failed attempts are counted
before dispatch. A preexisting path of any kind refuses reuse. Cleanup acts
only on captured exact IDs, never by wildcard or force. Unknown or nonremoved
resources are reported for manual review. The consumed claim, journal and result
remain at `/var/tmp/project5-port-diag-5b-once`, even on failure.

For separate authorization only, from a clean Ubuntu/ext4 checkout with the
protected artifacts and existing Python environment, as UID/GID `10001:10001`:

```sh
python -m evaluation.container_port_diagnostic --execute-authorized-once <approved-exact-delivery-sha>
```

Authorize one execution of `container-port-diag-5b-once` at the named commit
and plan digest, on the pinned engine/image, within the ceilings above. No
Windows probe, real application, model request, download, fallback, or prior run
may be added. This is a diagnostic only; its success would not complete 5B.
