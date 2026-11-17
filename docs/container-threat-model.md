# Container trust and permission matrix

The local Docker operator, host administrator, UID 10001 relay/data owner and
corpus owner are trusted. Documents, questions, tool output, HTTP clients and
stored malformed indexes are untrusted. Neither local image packaging nor exact
source hashes authenticate a hostile owner-operated Ollama daemon. The existing
finite grounding grammar and conservative abstention limitations remain intact.

| Actor/component | Allowed | Denied by this packaging |
| --- | --- | --- |
| Browser on local host | Same-origin UI/API and bounded grounded questions | Public/LAN listener, arbitrary origins, administrative API |
| Nginx UID 101 | Read static assets, proxy namespace loopback API, ephemeral /tmp | SQLite/relay mounts, writes to image, capabilities, external network routes |
| Backend UID 10001 | Read selected index; configured UDS model capability; /tmp | Index mutation, host networking, arbitrary URLs/SQL/files, privileged mode, Docker socket |
| Five MCP tools | Existing list/inspect/retrieve/version/citation read-only schemas | Mutation, arbitrary code, external discovery, network browsing, messages/actions |
| Host relay UID 10001 | Same-UID peer; fixed loopback tags/embed/chat with pinned model names and finite shapes | TCP listener, other peers, downloads, arbitrary endpoints/credentials/tools |
| Maintenance CLI | Explicit preview/confirmation, selected index writes with services stopped | Network access, API-based mutation, implicit directory deletion/synchronization |
| Backup/restore owner | Private stopped-service snapshot; confirmed validated replacement | Overwriting arbitrary paths, unvalidated backup, silent temp reuse or automatic repair |
| Health probes | Liveness and read-only structural readiness | Model requests, metadata checks, inference, recovery writes |
| CI | Synthetic tests, Compose/static checks, public dependency/base-image builds | Ollama service, live model tests, running production containers, image publishing/deployment |

Controls include immutable Linux-amd64 base digests, hash-pinned runtime wheels,
npm integrity lock, build-context allowlist, non-root users, capability drop ALL,
no-new-privileges, read-only roots, bounded tmpfs/PIDs/CPU/memory/restarts and
explicit local bind mounts with creation disabled. Backend serving uses query-only
read transactions and pins the mounted directory/index/socket inode. Content,
schema, vector/model configuration, offsets and FTS correspondence are checked.
The response validator is unchanged and does not accept claims merely because a
container or model produced them.

This is not an OS sandbox against the Docker owner. `internal` networking reduces
reachability but does not protect unrelated host services from a hostile daemon
administrator. Container limits do not constrain external Ollama resources. Socket
presence is structural readiness, not a guarantee of live model availability.
Host UID mappings outside the documented default Linux configuration are not
automatically trusted. Thread/OS cancellation is cooperative until Docker's stop
deadline forces process termination; serving has no index-write authority.

No runtime container or real model acceptance is asserted by the 5A offline tests.
The local engine was unavailable. CI build success, native ASGI/Unix fixture tests
and future container runtime acceptance are distinct evidence categories.
