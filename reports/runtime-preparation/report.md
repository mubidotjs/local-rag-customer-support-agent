# Milestone 5B offline preparation

**Runtime readiness: BLOCKED. Actual runtime acceptance: NOT RUN.**
Source predecessor: `f267a6dbfd3a67aae1e8285e8cca3e05897b1539`.
The delivery commit containing this report identifies the preparation source;
exact-SHA workflow results must be checked on that commit, not the predecessor.

## Evidence

- [Read-only environment assessment](assessment.json): Desktop 4.98.0 / Engine
  29.9.2, Linux x86_64, four CPUs, cgroup v2, supported resource controllers.
  Current Windows named-pipe context cannot attest Linux UID/mount semantics.
  Ubuntu is stopped and the prepared Project 5 images are absent.
- [Frozen runtime plan](plan.json): SHA-256
  `3be4f849bff92b4e836328f4082b8a531259349814a3b145fac870602296c0e3`.
- [Preservation inventory](preservation.json): SHA-256
  `77fd1dccf602fc6669ef0d63965b03f7754357fac5e435d60041b48989d7e6df`.
  All **397** locally available protected files match, including all predecessor
  **367** artifacts and every tracked predecessor file. Original application,
  corpora, Dockerfiles, workflows, indexes, labels, consumed claims, browser/model
  evidence and historical reports are unchanged. Fresh CI may omit explicitly
  recorded untracked local artifacts; it verifies every available file.
- [Offline results](offline.json) and [runner coverage](coverage.json).
- [Operational procedure and permission/cleanup limits](../../docs/runtime-preparation.md).

## Offline acceptance

37 focused fake-transport tests pass. Complete Windows suites: **481 passed,
5 skipped** with optional investigation SDKs; **377 passed, 40 skipped** without
them. Vitest **4 passed**; synthetic Playwright **4 passed, 2 real-model cases
skipped**; TypeScript/Vite production build passed. New runner combined coverage
**90.66%** (statements 96.58%, branches 79.29%). No runtime security conclusion is
derived from those fakes. POSIX-only storage checks remain part of Linux CI.

Failure tests cover invalid UID/mounts, unavailable service, unhealthy startup,
corruption, unexpected socket/network/model configuration, resource/capability
violations, deadline/budget exhaustion, stale proof, existing paths/claims,
historical SQLite access, partial journals, failed stop without retry and
ownership-limited cleanup. Native Docker/HTTP methods are replaced by fakes;
their adapters are separately tested with fake process/connection objects.

The additional `Offline runtime preparation` workflow runs fake transports and
publishes coverage, with no runtime containers. The original three workflows
remain byte-identical, including their pinned image build checks. All four
exact-SHA results are required for delivery; see GitHub Actions for this commit.

## Readiness and next authorization

Fixed ID: `container-runtime-5b-once`.
Fixed Linux workspace: `/var/tmp/project5-runtime-5b-once`.
Repository claim `.local/container-runtime-5b-once.claim.json` and output
`reports/container-runtime-5b-once` remain absent. No Linux workspace was created;
absence inside stopped Ubuntu has not been attested. No actual container stack,
relay, model request, image download or live claim was used.

Owner preparation of Ubuntu native WSL integration, trusted UID/GID 10001,
Linux checkout/environment and source-labeled preloaded images is required.
The read-only checker must then return READY and record immutable image IDs.
No permission, network or model-policy bypass is proposed.

Future separately authorized invocation, Linux only:

```sh
python -m evaluation.container_runtime --execute-authorized-once
```

Approval must name the exact execution commit, frozen plan hash and read-only
verified image IDs, authorizing only this fixed model-disabled run: 600 seconds,
17 normal Docker actions, six maximum failure-cleanup commands, seven planned
metadata commands (ceiling eight), nine local HTTP requests, one startup, one
backend restart, final stop, zero runner retries or real model/cloud/download
requests. This preparation task does not authorize that invocation. No 5C work.
