# Milestone 5B single-use runtime acceptance: STOPPED

**Milestone 5B is not complete. This authorization is consumed.**

Execution source: `5b1719c068e96b317f59cd24f26aa883361978b3`.
Run: `container-runtime-5b-once`.
Frozen plan SHA-256:
`3be4f849bff92b4e836328f4082b8a531259349814a3b145fac870602296c0e3`.
The commit introducing this archive is the delivery SHA. No source, plan,
Dockerfile, image, historical report or test was changed for delivery.

## Result and limits

The exact authorized command ran once as Linux UID/GID 10001:10001. It stopped
with `ConnectionRefusedError: [Errno 111] Connection refused` on the first fixed
HTTP request, `GET http://127.0.0.1:5173/`. There was no response and no retry.
The frozen runner measured 19.309327 seconds; external launch elapsed 19.663
seconds. Both are below 600 seconds. The images were not rebuilt or retagged.

| Activity | Actual | Frozen execution ceiling |
| --- | ---: | ---: |
| Execution preflight Docker metadata commands | 7 | 8 |
| Ordered normal Docker actions | 7 | 17 |
| Failure-cleanup Docker commands | 6 | 6 |
| Fixed local HTTP attempts | 1; zero responses | 9 |
| Stack startups | 1 | 1 |
| Backend restarts | 0 | 1 |
| Normal final stop sequence | Not reached | 1 |
| Runner retries | 0 | 0 |
| Actual Ollama metadata/embedding/generation | 0 / 0 / 0 | 0 / 0 / 0 |
| Model downloads / cloud inference | 0 / 0 | 0 / 0 |

The separately required pre-execution read-only readiness checker made seven
additional Docker metadata commands before launching the frozen execution.
Thus there were **14 preflight/readiness metadata commands total**, **20 Docker
commands within execution** (7 metadata + 7 normal + 6 cleanup), and **27 Docker
commands across readiness and execution**. These counts are explicitly separated;
the standalone checker is not hidden inside the execution count. Git/GitHub
publication and offline CI operations are separate from runtime actions.

## Checks established before failure

The completed `inspect_started` and `probe_started` establish:

- Backend UID/GID 10001:10001; frontend configured user 101:101; both running,
  with zero automatic restarts. Docker health remained `starting`, not proven healthy.
- Read-only roots, dropped ALL capabilities, no-new-privileges, non-privileged
  runtime, bounded restart policy `on-failure:3`, CPU/memory/PID limits checked.
- Frontend limits: 0.25 CPU, 128 MiB, 32 PIDs; backend: 1 CPU, 768 MiB, 64 PIDs.
- Internal network creation, frontend-only publishing at 127.0.0.1:5173, backend
  sharing its namespace, no extra host gateway or model route.
- Ext4 bind, ownership 10001:10001, private directory 0700 and database 0600,
  read-only data mount, matching host/container device and inode identities.
- Backend process UID/GID, identity namespace maps, actual memory/CPU/PID cgroups,
  read-only root/data filesystems and unchanged source hashes checked in-container.
- Existing Guard/checked_index validated source offsets, chunk maps, FTS matching,
  schema, stored-vector shape/identity and DELETE journal before reporting the
  deliberately disabled model route. Query-only SQLite rejected DELETE.
- No model socket was present. Direct Guard reason was `model_disabled`.

The fictional database SHA-256 was identical at creation, in-container probe and
post-stop byte audit:
`eb88217100ded903b9625cf632d9ff79788e7154b8a0c2611e0dd44258dfa0bd`.
Its vectors are **deterministic fake 768D values, NOT Nomic inference**. Pinned-shaped
metadata exercises storage compatibility only. The copied fictional database
and two invented source files are included for independent offline inspection.

## Not established

Same-origin UI/API HTTP behavior, HTTP liveness 200, HTTP readiness 503, hostile
Origin/Host responses, restart/persistence, normal graceful-shutdown log checks
and stopped-writer backup/restore were not reached. Cleanup stops are not a
substitute for those acceptance checks. No second request, startup wait change,
networking probe, smoke call or new experiment was performed after the failure.

The cause of the refused host-loopback connection remains unresolved. The
recorded refusal does not establish whether service startup timing or Docker
Desktop/WSL published-port reachability caused it. Any investigation or changed
acceptance plan requires a new scoped authorization; this claim cannot resume.

## Cleanup and retention

All six frozen cleanup commands succeeded: inspect the two captured run-labeled
IDs; stop backend; stop frontend; remove backend; remove frontend; remove the
captured network. No force/prune, unrelated-resource operation, retry or extra
Docker inspection was performed. Successful exact-ID removal receipts are in
raw actions 08 through 13; no uncertain captured resource remains reported.

Captured frontend: `63d639844105e15c35fa8484e23909d0b20376063fc205acf476362cc5d733ef`.
Captured backend: `53d4d743f43256981a98c8af2aeca52f4b34a0cb645b405d07bcb851e3d34135`.
Captured network: `01a0870e02d361baeb647154556d3929ed5cc82f72c608ea45ab993d7fbbc543`.

The persistent claim, original output and fictional workspace remain on Ubuntu.
They were not deleted, resumed or reused. This archive copies their evidence
byte-for-byte to a distinct publication path. Offline delivery CI runs in a fresh
checkout and does not operate on the consumed execution workspace or claim.

## Provenance, images and preservation

Engine: `d86d0c2c-42a0-4420-9f0d-0027845314bf`, native Ubuntu ext4, dedicated account.

- Backend: `sha256:375ed722706d2bc40338803d48e15df32c3eade270ace39e23166d5e045f7b6f`.
- Frontend: `sha256:436dc7373b582d32ef61a5da7fdd6ba7dc3a76299ef7b3c58fb99196f8cf569c`.

Required source/context labels, exact image environments, entrypoints, users and
architecture passed the unchanged execution preflight. Source offline proof:
520 passed, 1 skipped at the exact execution commit. The original four source
CI workflows passed; delivery requires four exact-delivery-SHA offline workflows.
Offline CI does not rerun runtime acceptance.

All 397 protected historical files matched in the original Windows, execution
and delivery checkouts; all 33 protected ignored artifacts were transferred
byte-identically. New claim copy, original artifact manifest and database hash
were independently checked. No historical claim, corpus, label, index, model,
browser capture, prior report or Projects 1–4 resource was modified.

## Evidence map

- `raw/`: byte-identical original journal, checkpoint, action receipts, preflight,
  fixture, claim, machine report and original `artifacts.sha256.json`.
- `supporting/`: persistent claim copy, exact-source offline proof/log, readiness
  result and external launch output.
- `fictional-workspace/`: new fake-only database and Hazel/Birch source documents.
- `summary.json`: reviewed outcomes, counts, limits and explicit unverified stages.
- `preservation-after.json`: all 397 per-checkout hashes.
- `checksums.json`: publication files, excluding this checksum manifest itself.

Zero-model evidence is limited to this run: disabled configuration, no socket,
no model command/upstream and only one fixed frontend HTTP attempt, all visible
in the probe and action journal. This is not a packet-capture assertion about
unrelated host processes. No 5C work or additional runtime run was performed.
