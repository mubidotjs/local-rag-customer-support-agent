"""One-shot private ingress acceptance; no implicit live mode."""
import argparse
from dataclasses import replace
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import time

import ingress_proxy
from evaluation import container_port_diagnostic as pinned
from evaluation.container_runtime import PLAN_SHA as HISTORICAL_PLAN_SHA
from evaluation.container_runtime import PROBE, git, host_gate, preservation, validate_image_environment, verify_probe
from evaluation.lifecycle_live import Journal, StopRun, save_new, sha

ROOT = pinned.ROOT
BASE = ROOT / "reports/private-ingress-preparation"
PROPOSAL_SHA = "a070d9c984a874e99dca4b556dc0e298e8178810c3e19750cafd543d34f602b0"
PLAN_SHA = "cfdc9c141a54d04c42d16239d63eed28437cd600d09b0c66ec79358ead72a4ed"
SOURCE = "76080433afd48bee4ce1368788d297721150d75b"
RUN = "container-runtime-5b-private-uds-v1-once"
WORK = Path("/var/tmp/project5-5b-private-uds-v1-once")
INSTANCE = "acceptance-5b-uds-v1-once"
INGRESS = ingress_proxy.BASE / INSTANCE
CONTROL = ingress_proxy.BASE / (INSTANCE + "-control")
ROLE_LABEL = "org.project5.ingress-role"
RUN_LABEL = "org.project5.run"
NET = RUN + "-isolated"
IDS = re.compile(r"[0-9a-f]{64}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
META = ("context", "engine", "image_frontend", "image_backend")
NORMAL = ("create_network", "create_frontend", "start_frontend", "create_backend",
          "start_backend", "inspect_running", "inspect_network", "probe_frontend",
          "probe_backend", "restart_backend", "inspect_restarted", "probe_restarted",
          "stop_backend", "stop_frontend", "inspect_stopped", "logs_backend", "logs_frontend")
HTTP = (("GET", "/", 200), ("GET", None, 200), ("GET", "/health/live", 200),
        ("GET", "/api/health", 200), ("GET", "/health/ready", 503), ("POST", "/api/ask", 503),
        ("GET", "/health/live", 400), ("GET", "/health/live", 403),
        ("GET", "/health/live", 200), ("GET", "/health/ready", 503))
ARCHIVES = {
    "container-runtime-5b-once-consumed": ("217da0328f7361b5e81e7cdfc6d13efa0bc59d0d34b9d02e6c34652474b1d050", 31),
    "container-port-diag-5b-once-stopped": ("ff03d7b670d05f207df8410eb33095652bfaa14ab8aedeb653fb57d3b23e33d0", 5),
    "container-port-diag-5b-v4-once-complete": ("202de23b70c4378ad9ecc8fdf24d7f8f5853a3cb00293623df485d163fb5cb13", 5),
}
IMAGE_INPUTS = {
    "frontend": ("frontend", "containers/private-ingress.Dockerfile", "containers/private-ingress.Dockerfile.dockerignore",
                 "containers/ingress-entrypoint.sh", "containers/nginx-ingress.conf"),
    "backend": ("support_agent", ".dockerignore", "containers/backend.Dockerfile", "containers/requirements-runtime.lock"),
}


def check(condition, reason):
    if not condition:
        raise StopRun(reason)


def sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def plan():
    check(sha(BASE / "plan.json") == PROPOSAL_SHA, "prior_proposal_changed")
    path = BASE / "plan-executable-v2.json"
    check(sha(path) == PLAN_SHA, "executable_plan_changed")
    data = json.loads(path.read_bytes())
    compose = json.loads((ROOT / "compose.ingress.yaml").read_bytes())
    check_compose(compose)
    check(data["run_id"] == RUN and data["workspace"] == WORK.as_posix() and data["ingress_dir"] == INGRESS.as_posix()
          and data["handoff"] == CONTROL.as_posix() and data["metadata"] == list(META)
          and data["normal_actions"] == list(NORMAL) and data["limits"] ==
          {"seconds": 600, "metadata": 4, "normal": 17, "cleanup": 5, "docker_total": 26,
           "http_total": 10, "proxy_launches": 1, "model_requests": 0, "retries": 0},
          "executable_plan_contract")
    check(sha(BASE / "fixture.json") == data["fixture_sha256"], "fixture_changed")
    for name, digest in data["input_sha256"].items():
        check(sha(ROOT / name) == digest, "input_changed:" + name)
    return data


def check_compose(c):
    f, b = c["services"]["frontend"], c["services"]["backend"]
    network = c["networks"]["isolated"]
    check(set(c["services"]) == {"frontend", "backend"} and set(c["networks"]) == {"isolated"}
          and network == {"driver": "bridge", "internal": True, "enable_ipv6": False,
                          "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                                          "com.docker.network.bridge.inhibit_ipv4": "true"}}
          and f["networks"] == ["isolated"] and b["network_mode"] == "service:frontend"
          and f["user"] == "101:101" and b["user"] == "10001:10001"
          and b["environment"] == {"RAG_MODEL_MODE": "disabled"}, "compose_topology")
    for role, row in (("frontend", f), ("backend", b)):
        check(not row.get("ports") and row["cap_drop"] == ["ALL"] and row["read_only"] is True
              and row["security_opt"] == ["no-new-privileges:true"] and row["pull_policy"] == "never"
              and row["platform"] == "linux/amd64" and row["restart"] == "on-failure:3"
              and row["build"]["dockerfile"] == ("containers/private-ingress.Dockerfile" if role == "frontend"
                                                 else "containers/backend.Dockerfile")
              and len(row["volumes"]) == 1, "compose_service")
        m = row["volumes"][0]
        check(m["type"] == "bind" and m["bind"] == {"create_host_path": False, "propagation": "rprivate"}
              and (m["target"], m["read_only"]) == (("/run/rag-ingress", False) if role == "frontend"
                                                     else ("/var/lib/rag", True)), "compose_mount")
        if role == "frontend":
            check(m["source"] == "/var/lib/project5-ingress/local", "compose_production_path")


def source(commit):
    check(re.fullmatch(r"[0-9a-f]{40}", commit) and git("rev-parse", "HEAD").decode().strip() == commit
          and not git("status", "--porcelain", "--untracked-files=all"), "wrong_or_dirty_source")
    check(git("merge-base", "--is-ancestor", SOURCE, commit) == b"", "wrong_predecessor")
    plan()
    return {role: hashlib.sha256(git("ls-tree", "-r", commit, "--", *paths)).hexdigest()
            for role, paths in IMAGE_INPUTS.items()}


def history():
    check(sha(ROOT / "reports/runtime-preparation/plan.json") == HISTORICAL_PLAN_SHA, "original_5b_plan_changed")
    result = preservation(require_local=True)
    check(len(result["checked"]) == 397, "preservation_count")
    for name, (digest, count) in ARCHIVES.items():
        root = ROOT / "reports" / name
        check(sha(root / "checksums.json") == digest, "archive_manifest_changed")
        values = json.loads((root / "checksums.json").read_bytes())
        check(len(values) == count and all(sha(root / p) == v for p, v in values.items()), "archive_changed")
    for path, digest in ((pinned.ORIGINAL_PLAN, pinned.ORIGINAL_PLAN_SHA), (pinned.PREVIOUS_PLAN, pinned.PREVIOUS_PLAN_SHA),
                         (pinned.V3_PLAN, pinned.V3_PLAN_SHA), (pinned.PLAN, pinned.PLAN_SHA)):
        check(sha(path) == digest, "historical_plan_changed")
    archives = ROOT / "reports"
    v3 = json.loads((archives / "container-port-diag-5b-once-stopped/summary.json").read_bytes())
    for name, digest in v3["audit"]["raw_file_sha256"].items():
        check(sha(Path(v3["audit"]["path"]) / name) == digest, "raw_v3_audit_changed")
    v4 = json.loads((archives / "container-port-diag-5b-v4-once-complete/summary.json").read_bytes())
    for name, entry in v4["fs"]["modes"].items():
        if "sha256" in entry:
            check(sha(Path("/var/tmp") / name) == entry["sha256"], "raw_v4_audit_changed")
    historical_claim = Path("/home/project5-acceptance/local-rag-customer-support-agent-5b/.local/container-runtime-5b-once.claim.json")
    check(sha(historical_claim) == v4["preservation"]["original_claim_sha256"], "prior_claim_changed")
    return {"protected": 397, "archives": [31, 5, 5], "local_claim_and_audits": "verified"}


def image_labels(role, commit, inputs):
    return {"org.project5.source": commit, ROLE_LABEL: role, "org.project5.input": inputs[role]}


def private_directory(path, uid, gid, mode):
    st = path.lstat()
    check(path.resolve(strict=True) == path and stat.S_ISDIR(st.st_mode)
          and (st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)) == (uid, gid, mode), "unsafe_directory:" + path.name)
    return st.st_dev, st.st_ino


def host_contract():
    import grp
    import pwd

    host_gate()
    pinned.landlock_kernel()
    group = grp.getgrnam("docker").gr_gid
    check(pwd.getpwuid(10001).pw_name == "project5-acceptance"
          and group in os.getgroups() and set(os.getgroups()) <= {10001, group}, "operator_groups")
    check(not any(p.pw_uid == 101 for p in pwd.getpwall()), "host_uid101_collision")
    try:
        proxy_user = pwd.getpwuid(11018)
        proxy_group = grp.getgrgid(11017)
    except KeyError as error:
        raise StopRun("missing_host_provisioning") from error
    check(proxy_user.pw_name == "project5-ingress-proxy"
          and proxy_user.pw_gid == 11017 and proxy_group.gr_name == "project5-ingress"
          and set(os.getgrouplist("project5-ingress-proxy", 11017)) == {11017}, "proxy_account_groups")
    ingress_proxy.ext4_ingress(INGRESS)
    ingress_proxy.ext4_ingress(CONTROL)
    private_directory(ingress_proxy.BASE, 0, 0, 0o755)
    private_directory(INGRESS, 101, 11017, 0o2710)
    private_directory(CONTROL, 0, 0, 0o755)
    for sub, uid, gid in (("operator", 10001, 11017), ("proxy", 11018, 10001)):
        path = CONTROL / sub
        private_directory(path, uid, gid, 0o2750)
        check(not any(path.iterdir()), "handoff_already_used")


FRONT_PROBE = r'''
set -eu
p=/run/rag-ingress/http.sock
test "$(id -u):$(id -g)" = 101:101
i=0
while [ ! -S "$p" ]; do
    i=$((i+1)); [ "$i" -le 40 ]; sleep 0.1
done
test ! -L "$p"
kill -0 "$(cat /tmp/nginx.pid)"
awk 'NR>1 && $2=="00000000" {bad=1} END {exit bad+0}' /proc/net/route
awk 'NR>1 && $4=="0A" && $2!="0100007F:1F40" && $2 !~ /^0B00007F:/ {bad=1} END {exit bad+0}' /proc/net/tcp
awk 'NR>1 && $4=="0A" {bad=1} END {exit bad+0}' /proc/net/tcp6
stat -c '%i:%u:%g:%a:%h' "$p"
'''

def create_args(role, image, ids, work):
    backend = role == "backend"
    uid = 10001 if backend else 101
    data = str(work / "data") if backend else INGRESS.as_posix()
    target = "/var/lib/rag" if backend else "/run/rag-ingress"
    opts = ["create", "--pull=never", "--runtime=runc", "--name", RUN + "-" + role,
            "--label", RUN_LABEL + "=" + RUN, "--label", ROLE_LABEL + "=" + role,
            "--user", f"{uid}:{uid}", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges:true",
            "--init", "--restart=on-failure:3", "--stop-timeout=20",
            "--cpus", "1" if backend else ".25", "--memory", "768m" if backend else "128m",
            "--memory-reservation", "256m" if backend else "32m", "--pids-limit", "64" if backend else "32",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size=16m,uid={uid},gid={uid},mode=0700",
            "--log-driver=local", "--log-opt=max-size=1m", "--log-opt=max-file=3",
            "--network", "container:" + ids["frontend"] if backend else ids["network"],
            "--mount", f"type=bind,source={data},target={target},bind-propagation=rprivate"
                       + (",readonly" if backend else "")]
    for key in pinned.PROXY_ENV:
        opts.extend(["--env", key + "="])
    if backend:
        opts.extend(["--env", "RAG_MODEL_MODE=disabled"])
    return opts + [image]


def verify_image(row, role, image, commit, inputs):
    cfg = row["Config"]
    backend = role == "backend"
    expected = ["python", "-m", "support_agent.production"] if backend else ["/usr/local/bin/ingress-entrypoint"]
    check(row["Id"] == image and (row["Os"], row["Architecture"]) == ("linux", "amd64")
          and cfg["User"] == ("10001:10001" if backend else "101:101")
          and cfg["Entrypoint"] == expected and not cfg.get("Cmd") and not cfg.get("Volumes")
          and cfg.get("Healthcheck") and cfg["StopSignal"] == ("SIGTERM" if backend else "SIGQUIT")
          and all(cfg["Labels"].get(k) == v for k, v in image_labels(role, commit, inputs).items()),
          "image_contract:" + role)
    options = row["Config"].get("Env", [])
    validate_image_environment(options, role)
    check(not any("OLLAMA" in item or item.startswith(("RAG_MODEL", "LD_PRELOAD", "LD_LIBRARY_PATH")) for item in options),
          "image_model_or_loader_configuration")


def verify_container(row, role, ids, images, work, running):
    b = role == "backend"
    host, cfg, ns, state = row["HostConfig"], row["Config"], row["NetworkSettings"], row["State"]
    expected_mount = (str(work / "data"), "/var/lib/rag", False) if b else (INGRESS.as_posix(), "/run/rag-ingress", True)
    mounts = row["Mounts"]
    check(row["Id"] == ids[role] and row["Name"] == "/" + RUN + "-" + role and row["Image"] == images[role]
          and cfg["Labels"].get(RUN_LABEL) == RUN and cfg["Labels"].get(ROLE_LABEL) == role,
          "ownership_identity:" + role)
    opts = host["SecurityOpt"]
    check(cfg["User"] == ("10001:10001" if b else "101:101") and host["Runtime"] == "runc"
          and host["ReadonlyRootfs"] is True and host["Privileged"] is False and host["Init"] is True
          and host["CapDrop"] == ["ALL"] and not host.get("CapAdd")
          and opts in (["no-new-privileges:true"], ["no-new-privileges"])
          and not any(host.get(name) for name in ("Devices", "DeviceRequests", "ExtraHosts", "PidMode", "UsernsMode", "GroupAdd"))
          and (host["NanoCpus"], host["Memory"], host["MemoryReservation"], host["PidsLimit"])
          == ((1_000_000_000, 805306368, 268435456, 64) if b else (250_000_000, 134217728, 33554432, 32))
          and host["RestartPolicy"] == {"Name": "on-failure", "MaximumRetryCount": 3}, "container_privileges:" + role)
    check(len(mounts) == 1 and (mounts[0]["Source"], mounts[0]["Destination"], mounts[0]["RW"]) == expected_mount
          and mounts[0]["Type"] == "bind" and mounts[0]["Propagation"] == "rprivate", "unsafe_mount:" + role)
    check(not host.get("PortBindings") and not host.get("PublishAllPorts")
          and all(not x for x in (ns.get("Ports") or {}).values()), "unexpected_docker_port")
    if b:
        check(host["NetworkMode"] == "container:" + ids["frontend"] and not ns.get("Networks")
              and [x for x in cfg["Env"] if x.startswith("RAG_MODEL_MODE=")] == ["RAG_MODEL_MODE=disabled"]
              and not any("OLLAMA" in x for x in cfg["Env"]), "unsafe_backend_network_or_model")
    else:
        check(host["NetworkMode"] in {ids["network"], NET} and (
                  (not running and not ns["Networks"]) or
                  (set(ns["Networks"]) == {NET} and ns["Networks"][NET]["NetworkID"] == ids["network"]
                   and not ns["Networks"][NET]["Gateway"] and not ns["Networks"][NET]["IPv6Gateway"])), "unsafe_frontend_network")
    check(state["Running"] is running and not state.get("OOMKilled") and not state.get("Restarting")
          and not state.get("Dead") and row["RestartCount"] == 0, "unexpected_container_state")
    if not running:
        check(state["ExitCode"] == 0, "unexpected_shutdown_exit")


def verify_network(row, ids):
    check(row["Id"] == ids["network"] and row["Name"] == NET and row["Driver"] == "bridge"
          and row["Internal"] is True and row["EnableIPv6"] is False
          and row["Labels"].get(RUN_LABEL) == RUN and set(row["Containers"]) == {ids["frontend"]}
          and row["Options"].get("com.docker.network.bridge.gateway_mode_ipv4") == "isolated"
          and row["Options"].get("com.docker.network.bridge.inhibit_ipv4") == "true", "unexpected_network")


def network_args():
    return ["network", "create", "--driver", "bridge", "--internal", "--ipv6=false",
            "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
            "--opt", "com.docker.network.bridge.inhibit_ipv4=true", "--label", RUN_LABEL + "=" + RUN, NET]


def new_index(work):
    from support_agent.config import Settings
    from support_agent.embeddings import Binding, PINNED_MODEL, PINNED_DIGEST, config_digest
    from support_agent.store import ingest

    directory = work / "data"
    inputs = work / "sources"
    for p in (directory, inputs):
        p.mkdir(mode=0o700)
    fixture = json.loads((BASE / "fixture.json").read_bytes())
    for d in fixture["documents"]:
        path = inputs / (d["id"] + ".md")
        path.write_text(f"---\nid: {d['id']}\ntitle: {d['title']}\nversion: {d['version']}\ntopic: setup\n---\n\n{d['text']}\n", encoding="utf-8")
    db = directory / "support.sqlite3"
    settings = replace(Settings(), db_path=db, managed_embeddings=True)
    class Synthetic:
        binding = Binding(PINNED_MODEL, PINNED_DIGEST, config_digest(settings))
        def embed(self, texts):
            return [[1.0] + [0.0] * 767 for _ in texts]
    ingest(inputs, settings, Synthetic())
    db.chmod(0o600)
    check(sqlite_signature(db)["counts"] == [2, 2, 2], "fixture_shape")
    s, d = db.stat(), directory.stat()
    return {"index_sha256": sha(db), "database_identity": [s.st_dev, s.st_ino],
            "data_identity": [d.st_dev, d.st_ino]}


def sqlite_signature(path):
    check(path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1
          and all(not os.path.lexists(str(path) + ext) for ext in ("-wal", "-shm", "-journal")), "unsafe_index")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        check(db.execute("PRAGMA quick_check").fetchone() == ("ok",)
              and db.execute("PRAGMA journal_mode").fetchone() == ("delete",), "sqlite_integrity")
        counts = [db.execute("SELECT count(*) FROM " + t).fetchone()[0] for t in ("documents", "chunks", "chunks_fts")]
        docs = db.execute("SELECT id, sha256 FROM documents ORDER BY id").fetchall()
        fts = db.execute("SELECT chunk_id,text FROM chunks_fts ORDER BY chunk_id").fetchall()
        spans = db.execute("SELECT c.id,c.text,c.source_start,c.source_end,d.source FROM chunks c "
                           "JOIN documents d ON d.id=c.document_id ORDER BY c.id").fetchall()
        check(fts == [(r[0], r[1]) for r in spans] and all(src[start:end] == text
              for _, text, start, end, src in spans), "fts_or_source_span")
    return {"counts": counts, "docs": docs, "fts": fts}


def backup_restore(work, expected):
    dest = work / "backup"
    dest.mkdir(mode=0o700)
    src = work / "data/support.sqlite3"
    check(sha(src) == expected["index_sha256"], "index_changed_before_backup")
    signature = sqlite_signature(src)
    backup = dest / "backup.sqlite3"
    restored = dest / "restore.sqlite3"
    for source_db, target in ((src, backup), (backup, restored)):
        with target.open("xb"):
            pass
        target.chmod(0o600)
        with sqlite3.connect(source_db.as_uri() + "?mode=ro", uri=True) as reader, sqlite3.connect(target) as writer:
            reader.backup(writer, pages=-1)
        check(sqlite_signature(target) == signature and stat.S_IMODE(target.stat().st_mode) == 0o600, "restore_integrity")
    check(sha(src) == expected["index_sha256"], "live_fixture_changed_during_restore")
    return {"source_sha256": sha(src), "backup_sha256": sha(backup), "restore_sha256": sha(restored)}

def handoff_write(directory, name, value):
    # Exclusive rename-free publication: readers see only fsynced complete content.
    scratch = directory / (name + ".writing")
    final = directory / name
    payload = (json.dumps(value, sort_keys=True) + "\n").encode()
    fd = os.open(scratch, os.O_WRONLY | os.O_NOFOLLOW | os.O_CREAT | os.O_EXCL, 0o640)
    with os.fdopen(fd, "wb") as out:
        os.fchmod(out.fileno(), 0o640)
        out.write(payload)
        out.flush()
        os.fsync(out.fileno())
    os.link(scratch, final, follow_symlinks=False)
    scratch.unlink()
    sync(directory)


def handoff_read(directory, name, owner, group):
    fd = os.open(directory / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as reader:
        s = os.fstat(reader.fileno())
        check(stat.S_ISREG(s.st_mode) and s.st_uid == owner and s.st_gid == group
              and stat.S_IMODE(s.st_mode) == 0o640 and s.st_nlink in {1, 2} and s.st_size <= 2048,
              "unsafe_handoff_receipt")
        return json.loads(reader.read(2049))


def await_handoff(directory, name, owner, group, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            return handoff_read(directory, name, owner, group)
        except FileNotFoundError:
            time.sleep(.08)
    raise pinned.Deadline("host_proxy_handoff_timeout:" + name)


class Native:
    def __init__(self, audit):
        self.docker = pinned.Transport(audit)

    def binding(self):
        return self.docker.binding()

    def command(self, argv, seconds, launched):
        return self.docker.command(argv, seconds, launched)

    def setup(self, work):
        return new_index(work)

    def proxy(self, stage, nonce, commit, inode, seconds, deadline):
        own, other = CONTROL / "operator", CONTROL / "proxy"
        private_directory(own, 10001, 11017, 0o2750)
        private_directory(other, 11018, 10001, 0o2750)
        params = {"run": RUN, "source": commit, "nonce": nonce, "inode": inode, "deadline": deadline}
        handoff_write(own, stage + ".json", params)
        if stage == "start":
            print(json.dumps({"host_proxy": "start_as_11018_now", "receipt_for": RUN}), flush=True)
        result = await_handoff(other, stage + ".json", 11018, 10001, seconds)
        check(result == {**params, "stage": stage, "uid": 11018, "gid": 11017}, "invalid_proxy_receipt")
        return result

    def http(self, method, target, host, origin, payload, seconds):
        connection = http.client.HTTPConnection("127.0.0.1", 5173, timeout=seconds)
        try:
            headers = {"Host": host, "Connection": "close", "Accept": "*/*"}
            if origin:
                headers["Origin"] = origin
            if payload:
                headers["Content-Type"] = "application/json"
            with pinned.alarm(seconds):
                connection.request(method, target, body=payload, headers=headers)
                value = connection.getresponse()
                body = value.read(4_194_305)
                check(len(body) <= 4_194_304, "http_body_limit")
                return value.status, value.getheader("Content-Type", ""), body
        finally:
            connection.close()

    def backup(self, work, expected):
        return backup_restore(work, expected)


class Coordinator:
    def __init__(self, transport, work, commit, images, inputs, verify, *, clock=time.monotonic, started=None):
        check(set(images) == {"frontend", "backend"} and all(IMAGE.fullmatch(x) for x in images.values())
              and images["frontend"] != images["backend"], "two_exact_image_ids_required")
        self.t, self.work, self.commit, self.images, self.inputs = transport, work, commit, images, inputs
        self.verify, self.clock = verify, clock
        self.started = clock() if started is None else started
        self.audit = work.with_name(work.name + ".preflight")
        self.count = dict(metadata=0, normal=0, cleanup=0, http=0, cli_launches=0, model=0)
        self.ids, self.stopped, self.removed = {}, set(), set()
        self.nonce = secrets.token_hex(24)
        self.uncertain, self.active_proxy, self.proxy_phase, self.claimed = None, False, None, False
        self.inode, self.asset, self.backend_started_at = None, None, None

    def remaining(self, cleanup=False):
        secs = (600 if cleanup else 525) - (self.clock() - self.started)
        if secs <= .1:
            raise pinned.Deadline("acceptance_deadline")
        return secs

    def binding(self):
        try:
            check(self.t.binding() == self.bound, "pinned_endpoint_changed")
        except (StopRun, OSError) as error:
            self.uncertain = self.uncertain or {"action": "identity_check", "error": str(error),
                                               "possibly_committed": False}
            raise pinned.TargetUncertain(str(error)) from error

    def write(self, event, **fields):
        self.journal.write({"event": event, **fields})

    def docker(self, action, argv, kind="normal", timeout=12):
        check(not self.uncertain, "uncertain_target_no_dispatch")
        expected = META if kind == "metadata" else NORMAL
        n = self.count[kind]
        check(sum(self.count[k] for k in ("metadata", "normal", "cleanup")) < 26, "docker_budget")
        if kind == "cleanup":
            check(n < 5 and action.startswith("cleanup_"), "cleanup_budget")
        else:
            check(n < len(expected) and expected[n] == action, "action_order_or_budget")
        check(self.claimed == (kind != "metadata"), "claim_gate")
        remaining = min(timeout, self.remaining(kind == "cleanup"))
        launched, result = False, None
        def record_launch():
            nonlocal launched
            check(not launched, "duplicate_launch")
            launched = True
            self.count["cli_launches"] += 1
            self.write("launch", action=action)
        try:
            self.binding()
            self.write("before", channel="docker", phase=kind, action=action, slot=n+1,
                       deadline_monotonic=self.clock()+remaining, argv=argv)
            self.count[kind] += 1
            result = self.t.command(argv, min(remaining, self.remaining(kind == "cleanup")), record_launch)
            check(launched, "missing_launch_receipt")
            self.write("receipt", action=action, data=result)
            self.binding()
            return result
        except BaseException as error:
            self.uncertain = {"action": action, "error": str(error), "launch_confirmed": launched,
                              "possibly_committed": kind != "metadata"}
            if action.startswith("create_") and isinstance(result, str) and IDS.fullmatch(result.strip()):
                self.uncertain["unconfirmed_created_id"] = result.strip()
            self.write("uncertain", **self.uncertain)
            raise pinned.TargetUncertain(str(error)) from error

    def structured(self, action, argv, verify):
        output = self.docker(action, argv)
        try:
            return verify(json.loads(output))
        except (ValueError, TypeError, KeyError, StopRun) as error:
            self.uncertain = {"action": action, "error": str(error), "possibly_committed": False}
            self.write("uncertain", **self.uncertain)
            raise pinned.TargetUncertain("resource_receipt_untrusted") from error

    def capture(self, role, output):
        ident = output.strip()
        if not IDS.fullmatch(ident) or ident in self.ids.values():
            self.uncertain = {"action": "create_" + role, "possibly_committed": True, "error": "untrusted_resource_id"}
            self.write("uncertain", **self.uncertain)
            raise pinned.TargetUncertain("untrusted_resource_id")
        self.ids[role] = ident
        self.write("owned_id", role=role, id=ident)

    def metadata(self):
        def get(action, args):
            return json.loads(self.docker(action, args, "metadata"))
        ctx = pinned.one(get("context", ["context", "inspect", "default"]))
        check(ctx["Name"] == "default" and ctx["Endpoints"]["docker"]["Host"] == pinned.ENDPOINT, "context_endpoint")
        info = get("engine", ["info", "--format", "{{json .}}"])
        sec = info["SecurityOptions"]
        check((info["ID"], info["OSType"], info["Architecture"], info["DefaultRuntime"], info["CgroupVersion"])
              == (pinned.ENGINE, "linux", "x86_64", "runc", "2")
              and any("name=seccomp" in x and "profile=builtin" in x for x in sec)
              and not any("rootless" in x or "userns" in x for x in sec)
              and all(info[key] is True for key in ("MemoryLimit", "PidsLimit", "CpuCfsPeriod", "CpuCfsQuota")),
              "engine_identity_or_security")
        for role in ("frontend", "backend"):
            image = self.images[role]
            verify_image(pinned.one(get("image_" + role, ["image", "inspect", image])), role, image, self.commit, self.inputs)
        self.verify()

    def reserve(self):
        check(not os.path.lexists(self.audit) and not os.path.lexists(self.work), "claim_or_audit_already_exists")
        self.remaining()
        self.bound = self.t.binding()
        check(self.bound is not None, "unbound_transport")
        self.audit.mkdir(mode=0o700)
        sync(self.audit.parent)
        for sub in ("cli-home", "cli-config"):
            (self.audit / sub).mkdir(mode=0o700)
        self.journal = Journal(self.audit / "actions.jsonl")
        save_new(self.audit / "intent.json", {"run": RUN, "source": self.commit, "plan": PLAN_SHA, "images": self.images})
        sync(self.audit)

    def seal(self):
        path = self.journal.path
        with path.open("r+b") as f:
            path.chmod(0o400)
            os.fsync(f.fileno())
        return sha(path)

    def create_claim(self):
        self.binding()
        self.remaining()
        prefix = self.journal.path.read_bytes()
        events = [json.loads(line) for line in prefix.splitlines()]
        check([(e["event"], e["action"]) for e in events]
              == [(stage, act) for act in META for stage in ("before", "launch", "receipt")], "unaudited_metadata")
        digest = self.seal()
        check(hashlib.sha256(prefix).hexdigest() == digest, "audit_changed")
        self.work.mkdir(mode=0o700)
        sync(self.work.parent)
        journal = Journal(self.work / "actions.jsonl")
        with journal.path.open("ab") as stream:
            stream.write(prefix)
            stream.flush()
            os.fsync(stream.fileno())
        check(sha(journal.path) == digest, "execution_audit_copy_changed")
        save_new(self.work / "claim.json", {"run": RUN, "source": self.commit, "plan": PLAN_SHA,
                                            "images": self.images, "preflight_sha256": digest, "preflight_bytes": len(prefix)})
        sync(self.work)
        self.journal = journal
        self.claimed = True

    def containers(self, action, roles, running):
        def verify(rows):
            check(isinstance(rows, list) and len(rows) == len(roles), "inspect_count")
            for role, row in zip(roles, rows, strict=True):
                verify_container(row, role, self.ids, self.images, self.work, running)
                if role == "backend" and running:
                    started = row["State"]["StartedAt"]
                    check(isinstance(started, str) and started and started != self.backend_started_at,
                          "backend_restart_not_attested")
                    self.backend_started_at = started
        self.structured(action, ["container", "inspect", *(self.ids[r] for r in roles)], verify)

    def proxy(self, stage, seconds):
        self.binding()
        self.write("before", channel="proxy_handoff", action=stage, inode=self.inode)
        try:
            result = self.t.proxy(stage, self.nonce, self.commit, self.inode, min(seconds, self.remaining(stage != "start")), self.started+600)
            check(result == {"run": RUN, "source": self.commit, "nonce": self.nonce, "inode": self.inode,
                             "deadline": self.started+600, "stage": stage, "uid": 11018, "gid": 11017}, "host_proxy_receipt")
        except (Exception, KeyboardInterrupt) as error:
            self.uncertain = self.uncertain or {"action": "proxy_" + stage, "error": str(error), "possibly_committed": True}
            raise pinned.TargetUncertain(str(error)) from error
        self.proxy_phase = {"start": "running", "stop": "observer", "socket_removed": "closed",
                            "cancel_observer": "closed"}[stage]
        self.write("receipt", action="proxy_" + stage, data=result)

    def http(self, slot):
        check(self.active_proxy and not self.uncertain and self.count["http"] == slot < 10, "http_order_or_budget")
        self.binding()
        method, path, status = HTTP[slot]
        if path is None:
            path = self.asset
        check(path is not None, "missing_asset")
        host = "example.invalid" if slot == 6 else "127.0.0.1:5173"
        origin = "http://example.invalid" if slot == 7 else None
        payload = b'{"question":"When is Lark setup complete?"}' if method == "POST" else None
        seconds = min(10, self.remaining())
        self.write("before", channel="http", slot=slot+1, method=method, path=path, host=host, origin=origin)
        self.count["http"] += 1
        actual, ctype, body = self.t.http(method, path, host, origin, payload, seconds)
        self.write("receipt", action="http_" + str(slot+1), status=actual, body_sha256=hashlib.sha256(body).hexdigest())
        check(actual == status, "unexpected_http_status:" + str(slot+1))
        if slot == 0:
            matches = re.findall(rb'<script[^>]+src="(/assets/[A-Za-z0-9_-]{1,128}\.js)"', body)
            check("text/html" in ctype and len(matches) == 1, "invalid_vite_entry")
            self.asset = matches[0].decode()
        if slot == 1:
            check("javascript" in ctype and bool(body), "invalid_vite_asset")
        if slot in (4, 5, 9):
            check(json.loads(body).get("detail") == "model_disabled", "model_disabled_required")

    def smoke(self):
        fixture = self.t.setup(self.work)
        self.write("fixture", data=fixture)
        self.capture("network", self.docker("create_network", network_args()))
        for role in ("frontend", "backend"):
            self.capture(role, self.docker("create_" + role, create_args(role, self.images[role], self.ids, self.work)))
            self.docker("start_" + role, ["start", self.ids[role]])
        self.containers("inspect_running", ("frontend", "backend"), True)
        self.structured("inspect_network", ["network", "inspect", self.ids["network"]],
                        lambda rows: verify_network(pinned.one(rows), self.ids))
        value = self.docker("probe_frontend", ["exec", self.ids["frontend"], "sh", "-ec", FRONT_PROBE])
        match = re.fullmatch(r"([0-9]+):101:11017:660:1", value.strip())
        check(match, "frontend_socket_identity_or_network")
        self.inode = int(match[1])
        python_hashes = {path.relative_to(ROOT).as_posix(): sha(path) for path in sorted((ROOT/"support_agent").rglob("*.py"))}
        argv = ["exec", self.ids["backend"], "python", "-c", PROBE, json.dumps(python_hashes)]
        verify_probe(json.loads(self.docker("probe_backend", argv)), fixture)
        self.proxy("start", 45)
        self.active_proxy = True
        for slot in range(8):
            self.http(slot)
        self.docker("restart_backend", ["restart", "--time", "20", self.ids["backend"]], timeout=35)
        self.containers("inspect_restarted", ("backend",), True)
        verify_probe(json.loads(self.docker("probe_restarted", argv)), fixture)
        for slot in (8, 9):
            self.http(slot)
        self.docker("stop_backend", ["stop", "--time", "20", self.ids["backend"]], timeout=35)
        self.stopped.add("backend")
        self.proxy("stop", 12)
        self.active_proxy = False
        self.docker("stop_frontend", ["stop", "--time", "20", self.ids["frontend"]], timeout=35)
        self.stopped.add("frontend")
        self.containers("inspect_stopped", ("frontend", "backend"), False)
        self.proxy("socket_removed", 12)
        for role in ("backend", "frontend"):
            self.docker("logs_" + role, ["logs", "--tail", "40", self.ids[role]])
        self.write("backup_restore", data=self.t.backup(self.work, fixture))

    def cleanup(self):
        for role in ("backend", "frontend", "network"):
            if role not in self.ids or self.uncertain:
                continue
            ident = self.ids[role]
            if role != "network" and role not in self.stopped:
                self.docker("cleanup_stop_" + role, ["stop", "--time", "20", ident], "cleanup", timeout=30)
                self.stopped.add(role)
            argv = ["network", "rm", ident] if role == "network" else ["rm", ident]
            self.docker("cleanup_remove_" + role, argv, "cleanup")
            self.removed.add(role)

    def run(self):
        self.reserve()
        status, failure = "STOPPED", None
        try:
            self.metadata()
            self.create_claim()
            self.smoke()
            status = "ACCEPTANCE_PASS"
        except (Exception, KeyboardInterrupt) as error:
            failure = str(error)
            if isinstance(error, (pinned.Deadline, TimeoutError, OSError, pinned.TargetUncertain, KeyboardInterrupt)):
                self.uncertain = self.uncertain or {"error": str(error), "possibly_committed": False}
        finally:
            if self.claimed:
                try:
                    if failure and not self.uncertain:
                        if self.proxy_phase == "running":
                            self.proxy("stop", 12)
                            self.active_proxy = False
                        if self.proxy_phase == "observer":
                            self.proxy("cancel_observer", 8)
                    if not self.uncertain:
                        self.cleanup()
                except (Exception, KeyboardInterrupt) as error:
                    self.uncertain = self.uncertain or {"error": str(error), "possibly_committed": False}
            try:
                checked = self.verify()
            except (Exception, KeyboardInterrupt) as error:
                checked = {"failure": str(error)}
                status = "STOPPED"
            unresolved = {k: v for k, v in self.ids.items() if k not in self.removed}
            if self.uncertain or unresolved:
                status = "STOPPED"
            outcome = {"status": status, "source": self.commit, "plan_sha256": PLAN_SHA,
                       "run": RUN, "images": self.images, "failure": failure, "counts": self.count, "claimed": self.claimed, "workspace_consumed": os.path.lexists(self.work),
                       "uncertainty": self.uncertain, "owned_ids": self.ids, "unresolved_ids": unresolved,
                       "preservation": checked, "journal_sha256": self.seal()}
            save_new((self.work if self.claimed else self.audit) / "result.json", outcome)
            sync(self.work if self.claimed else self.audit)
        return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--review-inputs", metavar="SHA")
    mode.add_argument("--check-host", metavar="SHA")
    mode.add_argument("--execute-authorized-once", metavar="SHA")
    parser.add_argument("--plan-sha256")
    parser.add_argument("--frontend-image")
    parser.add_argument("--backend-image")
    args = parser.parse_args()
    try:
        commit = args.review_inputs or args.check_host or args.execute_authorized_once
        if args.review_inputs:
            print(json.dumps({"source": commit, "plan": PLAN_SHA, "inputs": source(commit)}, indent=2))
            return 0
        if args.execute_authorized_once:
            check(args.plan_sha256 == PLAN_SHA, "explicit_plan_digest_required")
            check(not os.environ.get("PYTEST_CURRENT_TEST"), "fake_transport_required_in_tests")
        started = time.monotonic()
        with pinned.alarm(600):
            host_contract()
            inputs = source(commit)
            checked = history()
            check(not os.path.lexists(WORK) and not os.path.lexists(WORK.with_name(WORK.name + ".preflight")), "run_used")
            native = Native(WORK.with_name(WORK.name + ".preflight"))
            native.binding()
            if args.check_host:
                print(json.dumps({"source": commit, "plan": PLAN_SHA, "preservation": checked, "paths_unused": True}))
                return 0
            images = {"frontend": args.frontend_image or "", "backend": args.backend_image or ""}
            def verify():
                check(source(commit) == inputs, "source_drift")
                return history()
            result = Coordinator(native, WORK, commit, images, inputs, verify, started=started).run()
            print(json.dumps({k: result[k] for k in ("status", "claimed", "counts", "unresolved_ids")}))
            return int(result["status"] != "ACCEPTANCE_PASS")
    except (StopRun, OSError, ValueError) as error:
        print("STOPPED: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
