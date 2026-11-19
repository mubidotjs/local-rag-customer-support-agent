# Private Unix-socket ingress

See the [v2 executable readiness review](private-ingress-runtime-readiness.md) before considering a live run.

This is a separate production configuration, not an overlay on the archived
Compose file. Use `compose.ingress.yaml` by itself. It publishes no Docker
ports. HTTP travels from an unprivileged IPv4 loopback listener to the pinned
Nginx Unix socket. Nginx serves the UI and proxies only within its shared
namespace to the unchanged API listener at `127.0.0.1:8000`.

    Ubuntu browser -> 127.0.0.1:5173 [host proxy, 11018:11017]
        -> /var/lib/project5-ingress/local/http.sock
        -> Nginx [101:101] -> namespace loopback:8000 [API, 10001:10001]

The bridge is internal, IPv4-only, and has isolated gateway mode. Docker
[documents that this mode omits a bridge address](https://docs.docker.com/engine/network/port-publishing/#gateway-modes).
There is no default route, host networking, Docker socket mount, host gateway,
or TCP frontend listener in the revised design. The backend mounts only its
separate read-only index directory; model mode is disabled. The model socket
is not mounted. A privileged host or Docker administrator remains trusted.

## Identity and startup contract

| Item | Required owner and mode |
| --- | --- |
| Native Linux ext4 deployment parent `/var/lib/project5-ingress` | root:root, 0755; no symlink or writable ancestor |
| Dedicated mount source `/var/lib/project5-ingress/local` | UID 101 / GID 11017, mode 2710, initially empty |
| Nginx private staging directory | UID 101 / GID 11017, mode 2700 |
| Published HTTP socket | UID 101 / GID 11017, mode 0660, one link |
| Proxy identity | Host UID 11018 / GID 11017, no extra groups, including Docker |
| SQLite directory | Existing UID/GID 10001:10001 private storage contract, separate path |

The operator must provision and verify these dedicated numeric IDs and the
mount path before starting anything. Do not use UID 101 if it belongs to an
unrelated trusted service on that host. The socket group can traverse the
mount source and connect to the socket; it cannot list or modify the directory.
The frontend starts as 101:101. The setgid directory supplies GID 11017 to the
socket without adding it to the frontend's runtime supplementary groups.
No privileged startup, chmod 777, or permission fallback is used.

Nginx opens its [Unix listener](https://nginx.org/en/docs/http/ngx_http_core_module.html#listen)
in the private staging directory. The supervisor verifies the owned inode and
sets 0660 before linking it into the group-traversable directory with no replace.
Only that link remains when the proxy is admitted. The host proxy opens each
path component without following symlinks, requires native ext4 with no nested
ingress mount, pins the directory and socket inode, checks mode and link count,
and checks the connected Linux peer is UID/GID 101:101. A stale, missing,
replaced, or inaccessible endpoint closes the request with a bounded error
and is never silently adopted. A different listener requires an owner-reviewed
proxy restart.

For future operation only: supply approved preloaded image references as
`RAG_FRONTEND_IMAGE` and `RAG_BACKEND_IMAGE`, and the distinct private SQLite
directory as `RAG_DATA_DIR`. The dedicated Compose file fixes the production
socket mount source; do not combine it with the historical file. Under separate
runtime authorization, start the frontend and then the backend, wait for the
published socket, and launch `python -m ingress_proxy --instance local` as the
dedicated proxy account from a trusted read-only checkout. The proxy must not
inherit privileged groups or a Docker connection. A port conflict fails startup.
The frontend's container health check only establishes process/socket existence;
the acceptance plan separately requires real HTTP liveness and model-disabled
readiness (503) through the proxy.

Stop the proxy first: it closes the listener and cancels in-flight work. Then
stop the backend and frontend cleanly. The frontend forwards graceful QUIT
to Nginx and removes only its confirmed socket inode. A killed or crashed
frontend may leave a stale socket or staging path. Stop the proxy, prove the
frontend is stopped and the dedicated mount and inode are owned by this
deployment, then use separately authorized exact-path cleanup. Startup
refuses a nonempty mount rather than removing unknown entries. Restarting
only the backend does not replace the Nginx socket.

## HTTP and verification limits

The proxy accepts one HTTP/1.1 request per connection, on **IPv4 127.0.0.1:5173**
only. It accepts exactly `localhost:5173` or `127.0.0.1:5173` as Host; an Origin,
when supplied, must exactly match that Host. GET and HEAD cover reads; POST is
limited to JSON at `/api/ask`. The proxy forwards a fixed set of end-to-end
headers, will not use a request header as an upstream selector, and rejects
ambiguous framing, forwarding headers, upgrades and chunked requests. The
upstream is the fixed socket and receives one close-delimited HTTP/1.0
exchange. The downstream response preserves status and streams the bounded
body, adding chunk framing when the upstream length is absent. Unsupported
HTTP features fail closed. Limits are 16 active connections, 8 KiB headers,
4 KiB requests, 4 MiB responses, 7 seconds to read client input, 2 seconds to
connect, 10 seconds of streaming idle time, and 200 seconds total upstream
time. No prompts or bodies are logged by the proxy.

Loopback is a network boundary, **not authentication**; trusted local
processes with loopback access may use the app. Docker Desktop WSL2 must
still demonstrate that real Nginx can create this exact mount/socket and that
Linux peer credentials and mapping are correct. The synthetic tests do not
establish these properties for real Docker. Windows localhost routing and
browser access are outside this plan.

## Proposed acceptance

The [frozen plan](../reports/private-ingress-preparation/plan.json) defines
`container-runtime-5b-private-uds-v1-once`, a distinct workspace, [fictional
fixture](../reports/private-ingress-preparation/fixture.json), and no execution
in this milestone. It allows 27 Docker invocations (5 metadata, 17 normal,
up to 5 exact-ID cleanup), 10 Ubuntu HTTP attempts, one backend restart,
one proxy launch, and 600 seconds including failure cleanup. It checks
liveness, model-disabled readiness, strict Host/Origin behavior, read-only
SQLite and FTS, index byte persistence, graceful shutdown, and a stopped-writer
backup and restore to a separate fictional copy.

The plan is a reviewed proposal, not a runtime runner. A separate authorization
must bind the exact source SHA and plan/fixture digests to verified immutable
image IDs and an audited executable coordinator. Preparing/building images
and provisioning the isolated directory or account are separate prerequisites;
this delivery does none of them. Any Windows browser check needs its own
separate plan and authorization. Keep the previous stopped and diagnostic
claims untouched. No real Docker operations or model calls occurred here.
