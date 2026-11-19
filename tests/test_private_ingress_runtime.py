import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest

from evaluation import private_ingress_runtime as a
from evaluation import private_ingress_proxy as worker
from evaluation.container_runtime import IMAGE_ENVIRONMENTS

SOURCE = "a" * 40
IMAGES = {"frontend": "sha256:" + "b"*64, "backend": "sha256:" + "c"*64}
INPUTS = {"frontend": "d"*64, "backend": "e"*64}
IDS = {"network": "1"*64, "frontend": "2"*64, "backend": "3"*64}
FIXTURE = {"index_sha256": "0"*64, "data_identity": [1, 2], "database_identity": [1, 3]}


def good_probe():
    return FIXTURE | {"uid": 10001, "gid": 10001, "data_uid": 10001, "data_gid": 10001, "data_mode": 448,
                      "mode": "disabled", "model_socket_exists": False, "mutation_rejected": True,
                      "root_readonly": True, "data_readonly": True}


class Fake:
    def __init__(self, work):
        self.work = work
        self.actions = []
        self.requests = []
        self.handoffs = []
        self.fail = None
        self.drift = False
        self.bad_image = False
        self.bad_engine = False
        self.bad_ownership = False
        self.bad_mount = False
        self.bad_probe = False
        self.bad_http = None
        self.bad_reason = False
        self.bad_shutdown = False
        self.history = {"protected": 397, "archives": [31, 5, 5]}
        self.socket_reused = False
        self.backup_done = False
        self.ordered = []

    def binding(self):
        return "drifted" if self.drift else "original"

    def journal_dir(self):
        return self.work if self.work.exists() else self.work.with_name(self.work.name + ".preflight")

    def command(self, argv, timeout, launched):
        last = json.loads((self.journal_dir() / "actions.jsonl").read_bytes().splitlines()[-1])
        assert last["event"] == "before" and last["argv"] == argv
        action = last["action"]
        assert 0 < timeout <= 35
        assert (self.work / "claim.json").exists() is (action not in a.META)
        self.actions.append(action)
        self.ordered.append(action)
        launched()
        if self.fail == action:
            raise TimeoutError("lost fake receipt")
        if action == "context":
            return json.dumps([{"Name": "default", "Endpoints": {"docker": {"Host": a.pinned.ENDPOINT}}}])
        if action == "engine":
            return json.dumps({"ID": "other" if self.bad_engine else a.pinned.ENGINE, "DefaultRuntime": "runc",
                               "Architecture": "x86_64", "OSType": "linux", "CgroupVersion": "2",
                               "SecurityOptions": ["name=seccomp,profile=builtin"], "PidsLimit": True,
                               "MemoryLimit": True, "CpuCfsPeriod": True, "CpuCfsQuota": True})
        if action.startswith("image_"):
            role = action.split("_")[1]
            backend = role == "backend"
            config = {"User": "10001:10001" if backend else "101:101",
                      "Entrypoint": ["python", "-m", "support_agent.production"] if backend else ["/usr/local/bin/ingress-entrypoint"],
                      "Healthcheck": {"Test": ["CMD", "never"]}, "StopSignal": "SIGTERM" if backend else "SIGQUIT",
                      "Labels": a.image_labels(role, SOURCE, INPUTS),
                      "Env": [k + "=" + v for k,v in IMAGE_ENVIRONMENTS[role].items()]}
            if self.bad_image:
                config["Labels"]["org.project5.input"] = "mismatch"
            return json.dumps([{"Id": IMAGES[role], "Os": "linux", "Architecture": "amd64", "Config": config}])
        if action.startswith("create_"):
            role = action.split("_")[1]
            if role != "network":
                assert "--pull=never" in argv and argv[-1] == IMAGES[role]
                assert not any("publish" in arg or "11434" in arg for arg in argv)
                assert a.INGRESS.as_posix() in " ".join(argv) if role == "frontend" else str(self.work / "data") in " ".join(argv)
            return IDS[role]
        if action.startswith("inspect_"):
            if action == "inspect_network":
                return json.dumps([{"Id": IDS["network"], "Name": a.NET, "Driver": "bridge", "Internal": True,
                                    "EnableIPv6": False, "Labels": {a.RUN_LABEL:a.RUN},
                                    "Containers": {IDS["frontend"]: {}},
                                    "Options":{"com.docker.network.bridge.gateway_mode_ipv4":"isolated",
                                               "com.docker.network.bridge.inhibit_ipv4":"true"}}])
            roles = ["backend"] if action == "inspect_restarted" else ["frontend", "backend"]
            return json.dumps([self.container(role, action != "inspect_stopped") for role in roles])
        if action == "probe_frontend":
            return "17:101:11017:660:1\n"
        if action in {"probe_backend", "probe_restarted"}:
            probe = good_probe()
            if action == "probe_restarted" and self.bad_probe:
                probe["index_sha256"] = "other"
            return json.dumps(probe)
        if action.startswith("cleanup_"):
            assert argv[-1] in IDS.values() and "--force" not in argv
        return ""

    def container(self, role, running):
        back = role == "backend"
        net = "container:" + IDS["frontend"] if back else IDS["network"]
        source = str(self.work / "data") if back else a.INGRESS.as_posix()
        if self.bad_mount:
            source = "elsewhere"
        return {"Id": IDS[role], "Name": "/" + a.RUN + "-" + role, "Image": IMAGES[role], "RestartCount": 0,
                "State": {"Running": running, "ExitCode": 137 if self.bad_shutdown else 0,
                          "StartedAt": "after" if "restart_backend" in self.actions else "before"},
                "Config":{"User":"10001:10001" if back else "101:101",
                          "Labels":{a.RUN_LABEL:"other" if self.bad_ownership else a.RUN, a.ROLE_LABEL:role},
                          "Env":["RAG_MODEL_MODE=disabled"] if back else []},
                "HostConfig":{"NetworkMode":net,"Runtime":"runc","ReadonlyRootfs":True,"Privileged":False,"Init":True,
                              "CapDrop":["ALL"],"SecurityOpt":["no-new-privileges:true"],
                              "NanoCpus":1_000_000_000 if back else 250_000_000,
                              "Memory":805306368 if back else 134217728,"MemoryReservation":268435456 if back else 33554432,
                              "PidsLimit":64 if back else 32,"RestartPolicy":{"Name":"on-failure","MaximumRetryCount":3}},
                "Mounts":[{"Type":"bind","Source":source,"Destination":"/var/lib/rag" if back else "/run/rag-ingress",
                           "RW":not back,"Propagation":"rprivate"}],
                "NetworkSettings":{"Ports":{},"Networks":{} if back else
                                   {a.NET:{"NetworkID":IDS["network"],"Gateway":"","IPv6Gateway":""}}}}

    def setup(self, work):
        assert work == self.work and (work / "claim.json").exists()
        return FIXTURE

    def proxy(self, stage, nonce, commit, inode, timeout, deadline):
        assert inode == 17 and timeout > 0 and deadline > timeout
        self.ordered.append("proxy_" + stage)
        self.handoffs.append(stage)
        return dict(run=a.RUN, source=commit, nonce=nonce, inode=inode, deadline=deadline,
                    stage=stage, uid=11018, gid=11017)

    def http(self, method, path, host, origin, body, timeout):
        n = len(self.requests)
        self.ordered.append(f"http{n+1}")
        self.requests.append((method, path, host, origin, body))
        assert timeout <= 10
        if n == self.bad_http:
            return 500, "text/plain", b"wrong"
        if n == 0:
            return 200, "text/html", b'<script type="module" src="/assets/index-Qne7.js"></script>'
        if n == 1:
            assert path == "/assets/index-Qne7.js"
            return 200, "application/javascript", b"/* fictional */"
        if n == 6:
            assert host == "example.invalid"
        if n == 7:
            assert origin == "http://example.invalid"
        if n in (4, 5, 9):
            return 503, "application/json", b'{"detail":"different"}' if self.bad_reason else b'{"detail":"model_disabled"}'
        return a.HTTP[n][2], "application/json", b"{}"

    def backup(self, work, data):
        assert data == FIXTURE and self.actions[-1] == "logs_frontend"
        self.ordered.append("backup")
        self.backup_done = True
        return {"restored": True}


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    if not hasattr(os, "O_DIRECTORY"):
        monkeypatch.setattr(a, "sync", lambda path: None)
    monkeypatch.setattr(a.pinned.Transport, "command", lambda *args: pytest.fail("live Docker transport forbidden"))
    work = tmp_path / "fiction"
    fake = Fake(work)
    def verify():
        if isinstance(fake.history, Exception):
            raise fake.history
        return fake.history
    return a.Coordinator(fake, work, SOURCE, IMAGES, INPUTS, verify), fake


def test_executable_plan_and_frozen_proposal():
    plan = a.plan()
    assert a.sha(a.BASE / "plan.json") == a.PROPOSAL_SHA
    assert a.sha(a.BASE / "plan-executable-v2.json") == a.PLAN_SHA
    assert len(a.NORMAL) == 17 and len(a.HTTP) == 10
    assert plan["limits"]["docker_total"] == len(a.META) + len(a.NORMAL) + 5


def test_full_one_shot_and_private_audit_prefix(synthetic):
    run, fake = synthetic
    result = run.run()
    assert result["status"] == "ACCEPTANCE_PASS", result
    assert result["counts"] == dict(metadata=4, normal=17, cleanup=3, http=10, cli_launches=24, model=0)
    assert tuple(fake.actions[:21]) == a.META + a.NORMAL
    assert fake.handoffs == ["start", "stop", "socket_removed"] and fake.backup_done
    for first, next in (("start_frontend", "create_backend"), ("proxy_start", "http1"),
                        ("http8", "restart_backend"), ("restart_backend", "http9"),
                        ("proxy_stop", "stop_frontend"), ("inspect_stopped", "backup")):
        assert fake.ordered.index(first) < fake.ordered.index(next)
    prefix = (run.audit / "actions.jsonl").read_bytes()
    assert (run.work / "actions.jsonl").read_bytes().startswith(prefix)
    claim = json.loads((run.work / "claim.json").read_bytes())
    assert claim["preflight_sha256"] == hashlib.sha256(prefix).hexdigest()
    assert result["unresolved_ids"] == {}
    with pytest.raises(a.StopRun, match="already_exists"):
        run.run()


@pytest.mark.parametrize("key,expected", [("bad_engine", "engine_identity"), ("bad_image", "image_contract")])
def test_failed_metadata_has_no_claim(synthetic, key, expected):
    run, fake = synthetic
    setattr(fake, key, True)
    status = run.run()
    assert expected in status["failure"] and not status["claimed"] and not run.work.exists()
    assert status["counts"]["normal"] == status["counts"]["cleanup"] == 0


@pytest.mark.parametrize("attr", ["bad_ownership", "bad_mount", "bad_shutdown"])
def test_ambiguous_inspection_never_cleans(synthetic, attr):
    run, fake = synthetic
    setattr(fake, attr, True)
    status = run.run()
    assert status["status"] == "STOPPED" and status["uncertainty"]
    assert status["counts"]["cleanup"] == 0 and status["unresolved_ids"] == IDS


@pytest.mark.parametrize("failure", ["create_backend", "cleanup_remove_frontend"])
def test_uncertain_dispatch_stops_with_exact_remaining_ids(synthetic, failure):
    run, fake = synthetic
    fake.fail = failure
    status = run.run()
    assert status["status"] == "STOPPED" and status["uncertainty"]["possibly_committed"]
    assert fake.actions[-1] == failure
    if failure == "create_backend":
        assert status["counts"]["cleanup"] == 0 and set(status["unresolved_ids"]) == {"frontend", "network"}
    else:
        assert set(status["unresolved_ids"]) == {"frontend", "network"}


@pytest.mark.parametrize("kind", ["http", "ready", "persistence"])
def test_ordinary_disproof_stops_proxy_then_five_exact_cleanup_actions(synthetic, kind):
    run, fake = synthetic
    if kind == "http":
        fake.bad_http = 0
    elif kind == "ready":
        fake.bad_reason = True
    else:
        fake.bad_probe = True
    result = run.run()
    assert result["status"] == "STOPPED" and not result["uncertainty"], result
    assert result["counts"]["cleanup"] == 5 and result["unresolved_ids"] == {}
    assert fake.handoffs == ["start", "stop", "cancel_observer"]


def test_no_retry_on_endpoint_drift(synthetic, monkeypatch):
    run, fake = synthetic
    command = fake.command
    def drift(argv, secs, launched):
        receipt = command(argv, secs, launched)
        if fake.actions[-1] == "create_network":
            fake.drift = True
        return receipt
    monkeypatch.setattr(fake, "command", drift)
    result = run.run()
    assert result["uncertainty"]["unconfirmed_created_id"] == IDS["network"]
    assert result["counts"]["cleanup"] == result["counts"]["http"] == 0


def test_deadline_and_budget_gates(synthetic):
    run, fake = synthetic
    run.clock = lambda: run.started + 600
    with pytest.raises(a.StopRun, match="deadline"):
        run.run()
    assert not run.audit.exists() and not fake.actions
    run.clock = lambda: run.started
    run.count["normal"] = 17
    run.claimed = True
    with pytest.raises(a.StopRun, match="budget"):
        run.docker("start_backend", [])
    run.count["http"] = 10
    run.active_proxy = True
    with pytest.raises(a.StopRun, match="budget"):
        run.http(10)


def test_preservation_failure_before_mutation_keeps_audit(synthetic):
    run, fake = synthetic
    fake.history = a.StopRun("artifact_drift")
    result = run.run()
    assert not result["claimed"] and not fake.handoffs and "artifact_drift" in result["failure"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux SQLite and file permission contract")
def test_real_fictional_sqlite_persistence_integrity_and_backup_only(tmp_path):
    evidence = a.new_index(tmp_path)
    db = tmp_path / "data" / "support.sqlite3"
    assert a.sqlite_signature(db)["counts"] == [2, 2, 2]
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM documents")
    restored = a.backup_restore(tmp_path, evidence)
    assert restored["source_sha256"] == a.sha(db) == evidence["index_sha256"]
    assert a.sqlite_signature(tmp_path / "backup/restore.sqlite3") == a.sqlite_signature(db)
    with sqlite3.connect(db) as connection:
        connection.execute("DELETE FROM chunks_fts")
    with pytest.raises(a.StopRun, match="fts_or_source_span"):
        a.sqlite_signature(db)


def test_proxy_observer_requires_owned_echo_and_shutdown(monkeypatch, tmp_path):
    async def test():
        monkeypatch.setattr(a, "INGRESS", tmp_path)
        params = {"inode":17, "nonce":"nonce"}
        marker = tmp_path / "http.sock"
        marker.touch()
        def read(directory, name, uid, gid):
            if name == "socket_removed.json":
                return params
            raise FileNotFoundError
        events = []
        monkeypatch.setattr(a, "handoff_read", read)
        monkeypatch.setattr(worker, "acknowledge", lambda stage, raw: events.append((stage, raw)))
        with pytest.raises(a.StopRun, match="stale_frontend"):
            await worker.observe_stop(params, a.time.monotonic()+1, asyncio.Event())
        assert not events
        marker.unlink()
        await worker.observe_stop(params, a.time.monotonic()+1, asyncio.Event())
        assert events == [("socket_removed", params)]
    asyncio.run(test())


def test_failed_proxy_start_is_ambiguous_and_never_replays(synthetic, monkeypatch):
    run, fake = synthetic
    def fail(stage, *args):
        fake.handoffs.append(stage)
        raise TimeoutError("no host proxy acknowledgement")
    monkeypatch.setattr(fake, "proxy", fail)
    result = run.run()
    assert fake.handoffs == ["start"] and result["status"] == "STOPPED"
    assert result["uncertainty"]["action"] == "proxy_start"
    assert result["counts"]["http"] == result["counts"]["cleanup"] == 0
    assert result["unresolved_ids"] == IDS


def test_bad_source_compose_cannot_publish_or_broaden_network():
    from copy import deepcopy
    compose = json.loads((a.ROOT / "compose.ingress.yaml").read_bytes())
    a.check_compose(compose)
    for key, value in (("ports", ["0.0.0.0:80:80"]), ("networks", ["external"]),
                       ("security_opt", []), ("user", "0:0")):
        bad = deepcopy(compose)
        bad["services"]["frontend"][key] = value
        with pytest.raises(a.StopRun):
            a.check_compose(bad)


@pytest.mark.parametrize("untrusted_listener", [False, True])
def test_host_proxy_companion_runs_only_verified_loopback_and_shuts_down(monkeypatch, tmp_path, untrusted_listener):
    async def exercise():
        monkeypatch.setattr(worker.sys, "platform", "linux")
        for function, value in (("getuid",11018),("geteuid",11018),("getgid",11017),("getegid",11017),("getgroups",[11017])):
            monkeypatch.setattr(worker.os, function, lambda v=value: v, raising=False)
        monkeypatch.setattr(asyncio.get_running_loop(), "add_signal_handler", lambda *_: None)
        monkeypatch.setattr(a, "source", lambda commit: INPUTS)
        monkeypatch.setattr(a, "private_directory", lambda *_: None)
        monkeypatch.setattr(worker.ingress_proxy, "ext4_ingress", lambda *_: None)
        monkeypatch.setattr(a, "INGRESS", tmp_path)
        monkeypatch.setattr(a, "CONTROL", tmp_path)
        (tmp_path / "proxy").mkdir()
        events = []
        marker = dict(run=a.RUN, source=SOURCE, nonce="f"*48, inode=17, deadline=a.time.monotonic()+20)
        async def start_message(name, seconds):
            assert name == "start" and seconds <= 5
            return marker
        def control(path, filename, uid, gid):
            assert (uid,gid) == (10001,11017)
            if filename in ("stop.json","socket_removed.json"):
                return marker
            raise FileNotFoundError
        def publish(path, filename, raw):
            assert path == tmp_path / "proxy"
            assert raw["inode"] == 17 and raw["deadline"] == marker["deadline"]
            events.append(filename)
        class Upstream:
            def __init__(self, path):
                assert path == tmp_path
                self.stamp = (0, 17)
            def close(self):
                events.append("close")
        class Listener:
            def getsockname(self):
                return ("0.0.0.0",5173) if untrusted_listener else ("127.0.0.1",5173)
        class Proxy:
            async def start(self):
                from types import SimpleNamespace
                self.server = SimpleNamespace(sockets=[Listener()])
                events.append("bind")
            async def stop(self):
                events.append("unbind")
        monkeypatch.setattr(worker, "message", start_message)
        monkeypatch.setattr(a, "handoff_read", control)
        monkeypatch.setattr(a, "handoff_write", publish)
        monkeypatch.setattr(worker.ingress_proxy, "PrivateSocket", Upstream)
        monkeypatch.setattr(worker.ingress_proxy, "Proxy", lambda value: Proxy())
        if untrusted_listener:
            with pytest.raises(a.StopRun, match="unexpected_listener"):
                await worker.serve(SOURCE)
            assert events == ["intent.json","bind","unbind","close"]
        else:
            await worker.serve(SOURCE)
            assert events == ["intent.json","bind","start.json","unbind","stop.json","socket_removed.json","close"]
    asyncio.run(exercise())


def test_restart_without_fresh_start_receipt_stops_without_cleanup(synthetic, monkeypatch):
    run, fake = synthetic
    actual = fake.container
    def unchanged(role, running):
        row = actual(role, running)
        row["State"]["StartedAt"] = "before"
        return row
    monkeypatch.setattr(fake, "container", unchanged)
    outcome = run.run()
    assert outcome["status"] == "STOPPED" and outcome["claimed"]
    assert outcome["uncertainty"]["error"] == "backend_restart_not_attested"
    assert outcome["counts"]["cleanup"] == 0 and outcome["counts"]["http"] == 8
    assert outcome["unresolved_ids"] == IDS
