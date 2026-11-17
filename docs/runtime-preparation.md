# Milestone 5B: model-disabled runtime acceptance preparation

**Current execution readiness: BLOCKED. No actual 5B runtime acceptance has run.**
This supplements the unchanged historical [5A guide](containers.md) and
[container threat model](container-threat-model.md). Its statements about the
earlier unavailable Engine remain historical; current observations are recorded
in [assessment.json](../reports/runtime-preparation/assessment.json).

## Compatibility assessment and smallest safe path

Read-only inspection found Docker Desktop 4.98.0 / Engine 29.9.2, Linux x86_64,
WSL2, cgroup v2, runc, four CPUs and about 7.7 GiB assigned memory. Windows uses
`desktop-linux` over a named pipe. Ubuntu is stopped; docker-desktop is running.
The latter's root process has identity UID/GID maps, but this does **not** attest
container mappings or an Ubuntu bind mount. No Project 5 images are installed.

The frozen runner requires a Linux orchestrator with UID/GID **10001:10001**,
native local `default` Docker context at `unix:///var/run/docker.sock`, the
observed Engine ID, ext4 `/var/tmp`, at least 1.5 GB available host memory and
rootful runc without rootless/userns/ECI remapping. Actual root/data read-only
mounts, inode/device identities and container mappings must subsequently pass
runtime probes. Windows NTFS/DrvFs paths cannot satisfy this plan.

The smallest safe alternative is owner preparation of the existing Ubuntu WSL2:
start it, explicitly enable/verify Docker Desktop integration, provision a trusted
UID/GID 10001 operator and readable Linux Python environment, and place the exact
clean checkout on its Linux filesystem. Docker access grants trusted local
administrative authority; do not grant it to an untrusted user. Read-only checks
must confirm the native endpoint and ext4 path before requesting runtime approval.
If Desktop's mount identities do not satisfy the existing contract, stop. Do not
remove the checks. A native rootful Linux host is an alternative requiring a new
owner-reviewed engine-specific plan; this frozen plan cannot switch engines.

Docker recommends Linux filesystem storage and explicit distribution integration
for WSL2: [WSL integration](https://docs.docker.com/desktop/features/wsl/) and
[WSL practices](https://docs.docker.com/desktop/features/wsl/best-practices).
Bind sources belong to the daemon environment; see [bind mounts](https://docs.docker.com/engine/storage/bind-mounts/).
These references do not substitute for this application's ownership checks.

This task did not boot Ubuntu, build/load/pull images, create containers, change
Docker settings, configure a socket, or contact native Windows Ollama. The Linux
workspace was not created by this task; its absence inside stopped Ubuntu remains
unverified. Repository claim and output are confirmed absent.

## Fixed inputs and future prerequisites

Plan: [plan.json](../reports/runtime-preparation/plan.json), SHA-256
`3be4f849bff92b4e836328f4082b8a531259349814a3b145fac870602296c0e3`.
It was fixed before the runner tests. Expected outcomes have not been changed.

| Fixed item | Value |
| --- | --- |
| Run ID | `container-runtime-5b-once` |
| Workspace | `/var/tmp/project5-runtime-5b-once` |
| Fictional DB | `/var/tmp/project5-runtime-5b-once/data/support.sqlite3` |
| Claim, relative to exact clean checkout | `.local/container-runtime-5b-once.claim.json` |
| Evidence | `reports/container-runtime-5b-once` |
| Docker project | `project5-runtime-5b-once` |
| Engine ID | `d86d0c2c-42a0-4420-9f0d-0027845314bf` |

Two preloaded images are required, `project5-backend:5b-prepared` and
`project5-frontend:5b-prepared`, built from unchanged predecessor application and
Dockerfile content. Owner builds must carry labels `org.project5.source` =
`f267a6dbfd3a67aae1e8285e8cca3e05897b1539` and `org.project5.context` =
`3691f6470f429d194e5b21eaffc75dc1e9df659141fb1d75dfb2c1c43de7d91a`.
The plan records each build-input hash. Image builds are **not** part of the
acceptance and were not performed locally in preparation. Labels attest a
trusted owner build, not a cryptographic registry signature. Read-only preflight
checks configuration and latches immutable image IDs; the future authorization
should name those IDs. Runtime uses only those IDs with `--pull=never`.

The two invented Hazel/Birch documents use deterministic fake 768-dimensional
vectors with pinned-shaped compatibility metadata solely to exercise storage
validation. `fixture.json` explicitly records `NOT_NOMIC_INFERENCE`. They are not
real Nomic embeddings and provide no semantic/model acceptance. The index is
created from scratch, never copied from an existing index. No fake UDS endpoint
is included: model mode remains `disabled`, with no relay, socket mount or model
network route. The production application, five MCP permissions and Compose
configuration are unchanged.

## Proposed one-use execution, not authorized here

After all prerequisites and separate explicit owner approval, from the exact
Linux checkout and its configured Python environment as UID/GID 10001:

```sh
python -m pytest -q > .local/runtime-5b-offline-tests.log 2>&1
# Only after the command above exits successfully:
python -c "from evaluation.container_runtime import record_offline_proof; record_offline_proof(0)"
python -m evaluation.container_runtime --check-preparation
# ONLY after READY and separate explicit authorization:
python -m evaluation.container_runtime --execute-authorized-once
```

Preparation checking is read-only and creates no live claim. The proof is bound
to exact HEAD, tested source hashes and the successful suite log. All exact-SHA
CI workflows must pass. Actual execution additionally checks a clean tree,
predecessor ancestry, added-file allowlist, frozen plan, image build inputs,
protected artifacts and unused fixed paths. No ID, path, model, resume or fallback
flags exist. A consumed or partial claim/output/workspace refuses reuse.

The ceiling is **600 seconds**, with work stopping at 540 seconds to reserve
cleanup time. There are seven read-only Docker preflight commands (ceiling eight),
17 ordered normal Docker actions, at most six failure-cleanup commands and nine
fixed local HTTP requests. CLI calls are limited to 20 seconds, HTTP to three
seconds, response/output sizes are bounded. One startup, one backend restart and
final stop; no retry or second run. Docker's inherited `on-failure:3` policy stays
bounded, and observed nonzero `RestartCount` fails acceptance. It is distinct from
runner retries (zero). No deliberate crash/restart-loop or OOM experiment is run.

The 17 Docker actions, in order, are:

1. Create internal network.
2. Create frontend.
3. Start frontend.
4. Create backend sharing frontend's network namespace.
5. Start backend; one fixed two-second wait.
6. Inspect both services/security/resource settings.
7. Execute read-only backend filesystem/index probe; seven HTTP checks.
8. Restart backend once; one fixed two-second wait.
9. Inspect unchanged IDs, frontend start time and changed backend start time.
10. Repeat read-only probe and two HTTP checks; compare database hash/identity.
11. Stop backend with bounded grace period.
12. Stop frontend.
13. Inspect stopped states; host-side isolated backup and explicitly confirmed
    restore using existing production helpers.
14. Capture bounded backend shutdown logs.
15. Remove exact backend ID.
16. Remove exact frontend ID.
17. Remove exact created network ID.

Host-side fixture creation, SQLite opens and backup/restore are also journaled
before acting. Every Docker/HTTP attempt is durably recorded before sending,
including failures. Claims are exclusive and persistent. Partial checkpoints and
captured IDs survive failures. No arbitrary host database may be opened by the
runner's SQLite guard.

## Runtime assertions and limitations

Checks cover UID/GID, ext4/mount identity, 0700 directory/0600 database contract,
source offsets, FTS/index integrity, query-only SQLite mutation rejection,
read-only data/root mounts, no unexpected socket, source code hashes, cgroups,
capabilities, no-new-privileges, explicit CPU/memory/PID/restart limits, namespace
and loopback-only published port. Normal HTTP checks prove same-origin UI/API,
liveness, disabled-model rejection, hostile Origin rejection and hostile Host
rejection. After restart the same fictional database must remain byte-identical.

`/health/ready` is expected to return **503 `model_disabled`**, both times;
`/health/live` returns 200. This proves deliberate degraded readiness, not a
model-ready service. Docker's backend health status may be starting/unhealthy in
this mode. The runner does not poll or retry until healthy. It does not exercise
generation, semantic retrieval, optional SDKs inside the minimal image, a real
UDS relay, or inference resource exhaustion. Existing offline regressions cover
application/investigation behavior separately.

Only captured, run-labeled container IDs are eligible for failure cleanup. No
force/prune, name-based fallback or host-wide removal occurs. Previously attempted
stop/removal actions are never automatically retried. Uncertain/failed cleanup is
reported for manual exact-ID inspection; a hard timeout/daemon loss can leave
owned resources. The runner retains the claim, immutable evidence, fictional
workspace and backup. It never promises cleanup after host failure.

Zero real-model requests is enforced by disabled mode, no relay/upstream,
internal-only networking, fixed commands and fake transports in preparation;
it is not a packet-capture claim about unrelated host processes. This task made
zero actual model, runtime-container or runtime-HTTP requests. No live acceptance
claim has been consumed. Milestone 5C remains out of scope.


## Narrow inherited-environment correction

The first Ubuntu read-only checker run stopped before claim creation because the
pinned Python base inherits `LANG=C.UTF-8` and the public build fingerprint
`GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305`. Image history recorded those
inherited layers. These entries belong solely to the backend; the frontend has
neither. The correction changes only this checker, its tests and this guide.
The frozen plan and every build input remain unchanged. Existing source ancestry
and added-file allowlist permit these edits; the image source/context labels
continue to attest the unchanged predecessor application. Images are not rebuilt
or retagged for a checker correction.

The checker now compares exact, independently inspected per-role key/value maps
rather than accepting arbitrary names with a Python/NGINX/NJS prefix. Only the
backend gains the two approved entries. All previously observed pinned values
remain accepted; malformed/duplicate/missing/unknown entries, altered values,
PATH/PYTHONPATH overrides, loader settings, proxies, credentials, cloud settings
and model routes fail closed. This tightens validation without expanding any
other variable family or changing image/user/platform/entrypoint/label checks.

Read-only inspected image IDs (unchanged):

- Backend: `sha256:375ed722706d2bc40338803d48e15df32c3eade270ace39e23166d5e045f7b6f`.
- Frontend: `sha256:436dc7373b582d32ef61a5da7fdd6ba7dc3a76299ef7b3c58fb99196f8cf569c`.

The historical preparation report and its BLOCKED observations are retained as
original evidence. Any new readiness result is a separate ignored local record,
not a rewrite of that report. Fresh full-suite proof must bind to the correction
commit before invoking only `--check-preparation`. This correction authorizes no
runtime acceptance, claim creation, model requests or container start. Linux
host readiness does not establish actual container mount/UID behavior; those
remain future runtime assertions under a separate explicit authorization.
