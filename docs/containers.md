# Local container operations (Milestone 5A)

These images package the existing support application. This milestone is offline:
no model requests, model downloads, production data changes or deployment.
The original `support_agent.api:app`, UI, CLI, five MCP tools and grounding code
are unchanged. Container runtime acceptance remains **pending 5B**: the local
Docker CLI is present but Docker Engine is unavailable. CI builds images; it does
not start these containers or an Ollama service.

## Architecture

```mermaid
flowchart LR
    B[Local browser: 127.0.0.1:5173] --> N[Nginx: non-root, static React]
    N -->|same namespace loopback:8000| A[FastAPI production boundary]
    A -->|read-only bind mount| D[(Local SQLite DELETE journal)]
    A -. explicit UDS mode only .-> U[Read-only Unix socket mount]
    U -.-> R[Owner-started Linux relay, UID 10001]
    R -. fixed 127.0.0.1:11434 .-> O[Existing owner-operated Ollama]
```

Frontend and backend share a private network namespace. Only the frontend's
`127.0.0.1:5173` is published; backend Uvicorn binds `127.0.0.1:8000` in that
namespace. The Compose network is `internal: true`. The frontend proxies the
original Host/Origin; the original localhost:5173 origin policy stays intact.
There is no new CORS wildcard, admin API or public investigation endpoint.

Model mode defaults to **disabled**. There is no `host.docker.internal`, host
networking, TCP relay listener, bundled Ollama, model volume or auto-pull command.
The explicit UDS override mounts only the relay directory, never a Docker socket.

## Supported platform and ownership contract

The pinned build target is **Linux amd64**, Python 3.13.18. Backend UID/GID is
**10001:10001**; frontend **101:101**. Runtime and data directories must be local,
not NFS/SMB/network shares. The data directory must be owned by 10001:10001 with
mode **0700**, and `support.sqlite3` by that identity with mode **0600**, one hard
link, no symlink/junction in its path. Do not mount a home directory or repository.
Source/index integrity checks still run before answering.

| Host | 5A status |
| --- | --- |
| Native Linux, rootful Engine, default UID mapping | Packaging target; runtime acceptance pending 5B |
| Native Engine and Ollama in the same WSL2 Linux distribution | Same architecture candidate; not runtime-verified here |
| Windows Docker Desktop with native Windows Ollama | Live connectivity unsupported; use the unchanged bare application |
| Docker Desktop VM socket sharing, macOS, rootless/userns remapping | Unsupported until separately tested with explicit UID/socket mapping |
| ARM images | Not included; do not remove platform/digest checks to force execution |

A Unix socket mounted through a Windows filesystem is not assumed to work.
Never bind Ollama to `0.0.0.0`, use a host gateway override or broaden model URLs
to bypass unsupported configurations. Ollama and the relay must occupy the same
Linux host network context. Docker operator/root and the UID 10001 service account
are trusted local authorities; this does not sandbox a malicious Docker owner.

Expect approximately **1 GiB** for the containers (backend 768 MiB, frontend
128 MiB, reservations 256/32 MiB), plus image/build storage. CPU ceilings are
1.0 and 0.25; PID ceilings 64 and 32. Builds need additional memory/storage.
For existing CPU-only Qwen/Nomic use, a host with at least **8 GiB RAM**, preferably
12 GiB+, and four CPU cores is a planning estimate, not measured capacity here.
External Ollama resources are outside container cgroups and require owner sizing.

## Preparation and build (no inference)

Create a **new** operator-selected data directory and separate backup directory.
An administrator must provision ownership once; containers never start as root
to chown arbitrary mounts. Example Linux provisioning (not executed by this task):

```sh
sudo install -d -o 10001 -g 10001 -m 0700 /absolute/private/project5-data
sudo install -d -o 10001 -g 10001 -m 0700 /absolute/private/project5-backups
```

Use `containers/local.env.example` as a Compose interpolation template, copied to
an ignored local file. It contains absolute host paths, not secrets. Never pass
the host's entire environment as container `env_file`. An absent directory is an
error: `create_host_path: false` prevents accidental root-owned empty directories.

```sh
docker compose --env-file .local/containers.env config
docker compose --env-file .local/containers.env build
```

No original corpus, database, evidence, claim or `.local` contents enter the image
context. `.dockerignore` is an allowlist. Python base, Node builder and Nginx base
are pinned to Linux-amd64 immutable manifests; Python runtime wheels have exact
versions and SHA-256 hashes. The unchanged npm lock includes version/integrity
pins. Runtime has no npm/Node, pytest or optional LangGraph/MCP SDK, and Python pip/
headers are removed. Optional investigation imports therefore fail clearly; the
base API still works. The original host optional environment remains supported.

This pins build inputs; it does not claim byte-identical OCI archives across
different Docker builders/timestamps. Base/dependency security updates require
an explicit reviewed lock/digest update and new CI. No vulnerability-free claim
is made by a successful build.

Do not start a live model path during 5A. The later owner-run command for an
offline/disabled stack is `docker compose --env-file .local/containers.env up -d`.
It is documented here for operations, **not executed in this milestone**. Missing
or corrupt indexes keep readiness false. No service auto-ingests or repairs data.

## Explicit local model route (requires separate live authorization)

Provision a distinct relay directory owned by 10001:10001 mode 0700. Run the relay
as that non-root service identity in the **same Linux OS as existing Ollama**:

```sh
python -m support_agent.production.relay --socket-dir /absolute/private/project5-relay
```

It refuses root/other identities, existing socket files and insecure directories.
It creates `ollama.sock` mode 0600, verifies Linux `SO_PEERCRED` UID 10001, permits
one request in flight, and forwards only GET `/api/tags` and validated POST
`/api/embed` or `/api/chat`. Bodies, headers, response sizes and I/O are finite.
It never forwards arbitrary paths, credentials, model names, tools, downloads,
HTTP redirects or environment proxies. It has no health-triggered model calls.
Normal SIGTERM/SIGINT closes clients and removes only its own pinned socket inode.
After a crash, the owner must verify the relay is stopped before explicitly
removing that one stale socket; startup never guesses or silently deletes it.

Only after the separate authorization and supported-host checks, use both files:

```sh
docker compose --env-file .local/containers.env -f compose.yaml -f containers/compose.uds.yaml config
# Later authorized operation only:
docker compose --env-file .local/containers.env -f compose.yaml -f containers/compose.uds.yaml up -d
```

The adapter still addresses `http://127.0.0.1:11434` but HTTPX's explicit **UDS
transport** carries it through the mounted socket. No container-to-host TCP bridge
address is introduced. It retains Nomic's exact digest, prefixes and preprocessing;
Qwen generation additionally verifies the existing pinned Qwen digest before/after
selection. New keys/providers/discovery are not supported.

## Health, bounded work and shutdown

- `/health/live`: local process liveness, no index/model calls.
- `/health/ready`: read-only index/schema/hash/offset/vector/FTS validation, DELETE
  journal mode and configured private socket identity. **No connection, metadata
  request or inference.** Disabled/missing routes return 503.
- Readiness is structural, not proof that a daemon is currently responsive or a
  model remains registered. A stale/unresponsive route fails the actual ask with
  503, without fallback or retries. Index/socket replacement requires restart.
- Original `/api/health` remains backward-compatible. Original `/api/ask` remains
  exact-grounded, with its one-inference lock and 429 behavior.
- Requests: 4,096 body bytes, 8,192 backend header bytes, 64 headers, five seconds
  to receive the body, 16 active requests. A shared monotonic 180-second request
  deadline bounds subsequent model I/O (each exchange at most 90 seconds).
- Readiness SQLite busy waits are at most 100 ms; progress checks stop long native
  queries. File/document/chunk/context limits remain inherited and unchanged.
- Backend SIGTERM drains up to ten seconds; Compose allows 20 seconds before
  force termination. Inference thread cancellation is cooperative, not instant.
  Read-only serving cannot partially mutate the index if the process is killed.
- A disconnected/timed-out client does not prove the external daemon stopped
  computation. The relay's upstream operation remains bounded by 90 seconds;
  shutdown cancels active relay tasks. No replacement/retry is sent automatically.
- Restart policy is **on-failure:3**. Docker health status alone does not restart
  a service. Investigate repeated failures rather than enabling infinite restarts.
- An OOM/PID limit may terminate work; restart checks reject inconsistent state.
  No queued request retry, document repair or automatic ingestion is performed.

HTTP logs contain only server-generated 32-hex request ID, fixed route label,
bounded method/status and elapsed milliseconds. Incoming request IDs, query
strings, prompts, documents, model bodies, headers and exception strings are not
logged. Uvicorn access logs and Nginx access/raw request error logs are off. Docker
service logs rotate at three 1 MiB files. CLI output is explicit owner-visible
metadata; maintenance log storage is disabled.

## Maintenance, backup, restore and interrupted writes

SQLite lifecycle requires **DELETE journal mode**. Do not switch to WAL or copy a
live journal/shm set. Stop the backend and all CLI writers before maintenance.
Keep backups on a separate private local directory; never delete the bind-mounted
data directory to fix a restart. Compose has no anonymous persistent volume.

The `maintenance` profile uses the same non-root backend image, a **read/write**
data bind mount, no network namespace connectivity, no public port and no service
health probe. A small offline wrapper sets umask 077, fixes the database argument,
and refuses real-embedding flags before calling the original CLI:

```sh
docker compose --env-file .local/containers.env stop backend
docker compose --env-file .local/containers.env run --rm --no-deps maintenance status
docker compose --env-file .local/containers.env run --rm --no-deps maintenance recover
```

`recover` is an explicit owner action after a crash, using existing validated
rollback-journal recovery; readiness never performs it. Inspect status before
restarting. Source paths/plans must be explicitly mounted and selected for the
original preview/apply workflow; destructive apply still requires its exact
confirmation token. The base maintenance profile is **network-disabled** and
does not support real embedding generation. Perform separately authorized live
ingestion with the existing same-host CLI under the data owner, then verify
ownership/permissions before serving. No live ingestion is authorized here.

New storage CLI, run as UID/GID 10001 (or via an explicit maintenance entrypoint
override with the private backup directory mounted):

```sh
python -m support_agent.production.volume check --directory /var/lib/rag
python -m support_agent.production.volume backup --directory /var/lib/rag --backup-directory /private/empty-backup --confirm WRITERS-STOPPED
python -m support_agent.production.volume restore --directory /var/lib/rag --backup-directory /private/backup --confirm REPLACE-STOPPED-INDEX
```

Backup requires an empty private destination, validates a byte-identical stopped
index, fsyncs it and writes a checksum manifest last. An interrupted unmanifested
copy is not a valid backup. Restore validates that manifest and source index,
requires explicit destructive confirmation, stages `restore.tmp` exclusively,
checks the old inode/hash for races, fsyncs and atomically replaces the index.
An interrupted staged file is not silently reused: inspect and explicitly remove
that specific temporary file only with writers stopped. Preserve the prior backup.
Restart serving after restore; a running reader intentionally rejects replacement.
These procedures assume cooperative owner-stopped writers, not a hostile owner
rewriting the corpus and every hash coherently.

## Troubleshooting and separate 5B proposal

| Symptom | Action |
| --- | --- |
| Engine pipe/socket unavailable | Do not claim runtime success; configure supported local Engine separately |
| Permission/startup rejection | Check numeric UID/GID, 0700 directory, 0600 regular single-link index; never chmod 777 |
| Ready 503, live 200 | Check index, DELETE journal/sidecars, mode and socket ownership; probes make no model calls |
| `index_replaced_restart_required` | Stop writers, verify intended restore, restart backend |
| Socket changed/stale | Stop backend/relay, inspect owner inode and remove only confirmed stale socket, restart explicitly |
| Ask 429 | One inference active; no automatic retry |
| Ask 503/timeout | Inspect local model route outside the application only under an authorized budget; do not add warm-ups |
| Optional SDK unavailable | Use unchanged host optional environment; no automatic installation/discovery |

Proposed **5B only after separate approval**: one fresh fictional Docker workspace,
supported Linux Engine, pinned built images, disabled/fake model route; verify
non-root identities, same-origin API, mount persistence across one restart,
read-only rejection, resource limits, health degradation and SIGTERM cleanup.
Use a fake UDS upstream for connectivity/permission tests. Suggested cap: one
bounded start/restart/stop sequence within ten minutes, **zero real Ollama
metadata/embedding/generation requests or model downloads**. This proposal is not
an implemented or executed 5B runner. Any genuine inference needs another explicit
plan and budget. Do not infer permission from these operating examples.

Primary references: [Compose services](https://docs.docker.com/reference/compose-file/services/),
[private networking](https://docs.docker.com/compose/how-tos/networking),
[UID mapping](https://docs.docker.com/engine/security/rootless/uid-gid-mapping/),
[HTTPX UDS transport](https://www.python-httpx.org/advanced/transports/).
