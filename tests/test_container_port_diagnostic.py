import copy
from contextlib import contextmanager
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Barrier
import json
import stat
from types import SimpleNamespace
from pathlib import Path

import pytest

from evaluation import container_port_diagnostic as d


def item(ports):
    return {"HostConfig": {"PortBindings": d.sole_target(d.PORT, d.LOCAL_PORT)},
            "NetworkSettings": {"Ports": ports}}


def test_preserved_inspection_reproduces_failure():
    receipt = d.ROOT / "reports/container-runtime-5b-once-consumed/raw/action-06.json"
    frontend = json.loads(json.loads(receipt.read_text())["stdout"])[0]
    d.require_requested(frontend, 8080, 5173)
    with pytest.raises(d.StopRun, match="ineffective"):
        d.require_effective(frontend, 8080, 5173)


@pytest.mark.parametrize("bad", [
    {}, None, [], {d.KEY: []}, {d.KEY: None}, {"18081/tcp": []},
    {d.KEY: [{"HostIp": "0.0.0.0", "HostPort": str(d.LOCAL_PORT)}]},
    {d.KEY: [{"HostIp": "::", "HostPort": str(d.LOCAL_PORT)}]},
    {d.KEY: [{"HostIp": "", "HostPort": str(d.LOCAL_PORT)}]},
    {d.KEY: [{"HostIp": "127.0.0.1", "HostPort": "80"}]},
    {d.KEY: [{"HostIp": "127.0.0.2", "HostPort": str(d.LOCAL_PORT)}]},
    {d.KEY: [{"HostIp": "127.0.0.1", "HostPort": d.LOCAL_PORT}]},
    {d.KEY: d.sole_target(d.PORT, d.LOCAL_PORT)[d.KEY] * 2},
    {**d.sole_target(d.PORT, d.LOCAL_PORT), "5555/tcp": None},
])
def test_effective_mapping_fails_closed(bad):
    with pytest.raises(d.StopRun, match="ineffective"):
        d.require_effective(item(bad), d.PORT, d.LOCAL_PORT)


def test_both_requested_and_effective_required():
    receipt = item(d.sole_target(d.PORT, d.LOCAL_PORT))
    d.require_effective(receipt, d.PORT, d.LOCAL_PORT)
    del receipt["HostConfig"]["PortBindings"]
    with pytest.raises(d.StopRun, match="requested"):
        d.require_effective(receipt, d.PORT, d.LOCAL_PORT)
    receipt = item(d.sole_target(d.PORT, d.LOCAL_PORT))
    del receipt["NetworkSettings"]
    with pytest.raises(d.StopRun, match="ineffective"):
        d.require_effective(receipt, d.PORT, d.LOCAL_PORT)


class Fake:
    ids = {"responder": "1" * 64, "sentinel": "2" * 64, "net_responder": "3" * 64, "net_sentinel": "4" * 64}

    def __init__(self, work, mode="empty", fail=None, mutate=None):
        self.work, self.mode, self.fail, self.mutate = work, mode, fail, mutate
        self.calls, self.http, self.ready = [], [], False
        self.engine, self.image, self.token = d.ENGINE, d.IMAGE, "same-local-endpoint"
        self.context, self.endpoint = "default", d.ENDPOINT
        self.image_labels = dict(d.IMAGE_LABELS)
        self.audit = work.with_name(work.name + ".preflight")

    def binding(self):
        return self.token

    def guard_paths(self):
        pass

    def event(self, transport):
        directory = self.work if self.work.exists() else self.audit
        events = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "before" and events[-1]["transport"] == transport
        return events[-1]["action"]

    def command(self, args, seconds, on_launch):
        action = self.event("docker")
        assert 0 < seconds <= 6
        self.calls.append((action, args))
        on_launch()
        if action in d.PREFLIGHT:
            assert not self.work.exists() and (self.audit / "intent.json").is_file()
        else:
            assert (self.work / "claim.json").is_file()
        if action == self.fail:
            raise d.StopRun("injected docker failure")
        if action == "context":
            return json.dumps([{"Name": self.context, "Endpoints": {"docker": {"Host": self.endpoint}}}])
        if action == "engine":
            return json.dumps({"ID": self.engine, "OSType": "linux", "Architecture": "x86_64",
                               "DefaultRuntime": "runc", "SecurityOptions": ["name=seccomp,profile=builtin"],
                               "CgroupVersion": "2", "MemoryLimit": True, "PidsLimit": True,
                               "CpuCfsQuota": True, "CpuCfsPeriod": True})
        if action == "image":
            return json.dumps([{"Id": self.image, "Os": "linux", "Architecture": "amd64",
                                "Config": {"User": "10001:10001", "Labels": self.image_labels,
                                           "Env": [f"{k}={v}" for k, v in d.IMAGE_ENVIRONMENTS["backend"].items()]}}])
        if action in ("network_responder", "network_sentinel", "create_responder", "create_sentinel"):
            if action.startswith("network"):
                assert "--internal" in args and "--ipv6=false" in args
                return self.ids["net_" + action.split("_")[1]]
            assert "--no-healthcheck" in args and "--pull" in args and "never" in args
            assert "--read-only" in args and "--cap-drop" in args and "ALL" in args
            assert args[-5:-1] == ["-I", "-B", "-S", "-c"]
            return self.ids[action.split("_")[1]]
        if action in ("inspect_containers", "inspect_networks"):
            return json.dumps([self.inspection(action, role) for role in ("responder", "sentinel")])
        if action == "inside_http":
            assert self.ready
            return json.dumps(d.EXPECTED)
        if action == "sentinel_control":
            return "sentinel_listening"
        if action == "blocked_egress":
            assert args[-1] == "172.21.0.2"
            return "cross_bridge_blocked"
        if action.startswith("cleanup"):
            assert args[-1] in self.ids.values()
            assert "--force" not in args
        return ""

    def inspection(self, action, role):
        responder = role == "responder"
        ident, net = self.ids[role], self.ids["net_" + role]
        name = d.TAG + "-" + role
        subnet = "172.20" if responder else "172.21"
        if action == "inspect_networks":
            value = {"Id": net, "Name": name, "Driver": "bridge", "Internal": True, "EnableIPv6": False,
                     "Labels": {"org.project5.diagnostic": d.RUN_ID},
                     "IPAM": {"Config": [{"Subnet": subnet + ".0.0/16"}]},
                     "Containers": {ident: {"IPv4Address": subnet + ".0.2/16"}}}
        else:
            bind = d.sole_target(d.PORT, d.LOCAL_PORT) if responder else {}
            ports = copy.deepcopy(bind)
            if responder and self.mode == "empty":
                ports = {d.KEY: []}
            if responder and self.mode == "wildcard":
                ports[d.KEY][0]["HostIp"] = "0.0.0.0"
            value = {
                "Id": ident, "Name": "/" + name, "Image": d.IMAGE, "State": {"Running": True},
                "Config": {"User": "10001:10001", "Labels": {"org.project5.diagnostic": d.RUN_ID},
                           "Entrypoint": ["/usr/local/bin/python"],
                           "Env": [f"{k}={v}" for k, v in d.IMAGE_ENVIRONMENTS["backend"].items()]
                                  + [f"{k}=" for k in d.PROXY_ENV],
                           "Cmd": ["-I", "-B", "-S", "-c", d.RESPONDER if responder else d.SENTINEL]},
                "HostConfig": {"NetworkMode": net, "ReadonlyRootfs": True, "Privileged": False, "CapDrop": ["ALL"],
                               "SecurityOpt": ["no-new-privileges:true"], "PidsLimit": 24, "NanoCpus": 500_000_000,
                               "Memory": 134217728, "MemorySwap": 134217728,
                               "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0}, "PortBindings": bind},
                "Mounts": [{"Type": "bind", "Source": str(self.work / "ingress"), "Destination": "/ingress",
                            "RW": True, "Propagation": "rprivate"}] if responder else [],
                "NetworkSettings": {"Networks": {name: {"NetworkID": net, "Gateway": "", "IPv6Gateway": "",
                                                        "IPAddress": subnet + ".0.2"}}, "Ports": ports},
            }
        if self.mutate and responder:
            self.mutate(value)
        return value

    def precheck_port(self):
        if self.fail == "bound_port":
            raise OSError("port unavailable")

    def wait_listener(self, path, seconds):
        assert path == self.work / "ingress/responder.sock" and 0 < seconds <= 5
        if self.fail == "listener":
            raise d.StopRun("listener_not_ready")
        self.ready = True

    def request(self, action, path, seconds):
        assert self.ready and self.event("http") == action and 0 < seconds <= 3
        self.http.append(action)
        if action == self.fail:
            raise ConnectionRefusedError()
        return d.EXPECTED


@pytest.fixture
def session(tmp_path, monkeypatch):
    work = tmp_path / "fictional-only"
    fake = Fake(work)
    fake.verify_socket = lambda: None
    instance = d.Session(fake, work)
    # Filesystem semantics of the permanent claim are exercised on tmp_path;
    # the POSIX fsync syscall is not available on the Windows test host.
    if not hasattr(d.os, "O_DIRECTORY"):
        monkeypatch.setattr(instance, "sync_directory", lambda _: None)
    return instance, fake


def run(instance):
    return instance.run("a" * 40, lambda: {"synthetic_preservation": True})


@pytest.mark.parametrize(("mode", "http_count", "published"), [
    ("empty", 2, "skipped_no_effective_mapping"), ("bound", 3, d.EXPECTED),
])
def test_complete_fake_sequence(session, mode, http_count, published):
    instance, fake = session
    fake.mode = mode
    outcome = run(instance)
    assert outcome["status"] == "DIAGNOSTIC_COMPLETE"
    assert not outcome["unresolved_cleanup"] and outcome["actual_model_requests"] == 0
    assert outcome["results"]["ubuntu_published_http"] == published
    assert outcome["results"]["cross_bridge_tcp"] == "denied_with_positive_control"
    assert outcome["http_attempts"] == http_count
    assert outcome["normal_docker"] == outcome["normal_cli_launches"] == 14
    assert outcome["cleanup_docker"] == outcome["cleanup_cli_launches"] == 6
    assert [a for a, _ in fake.calls[:14]] == list(d.ORDER)
    assert outcome["journal_sha256"] == d.sha(instance.work / "actions.jsonl")
    prefix = (instance.audit / "actions.jsonl").read_bytes()
    claim = json.loads((instance.work / "claim.json").read_bytes())
    assert claim["preflight_bytes"] == len(prefix)
    assert claim["preflight_sha256"] == outcome["preflight_sha256"] == hashlib.sha256(prefix).hexdigest()
    assert (instance.work / "actions.jsonl").read_bytes().startswith(prefix)
    assert [row["action"] for row in map(json.loads, prefix.splitlines()) if row["event"] == "before"] == list(d.PREFLIGHT)
    assert not (instance.audit / "result.json").exists()
    with pytest.raises(d.StopRun, match="already_exists"):
        run(instance)
    assert len(fake.calls) == 20


@pytest.mark.parametrize(("failure", "http_count"), [
    ("start_responder", 0), ("listener", 0), ("inside_http", 1),
    ("sentinel_control", 1), ("blocked_egress", 1), ("ubuntu_published_http", 2),
    ("ubuntu_private_socket_http", 3), ("cleanup_remove_responder", 3),
])
def test_single_failure_consumes_claim_and_never_retries(session, failure, http_count):
    instance, fake = session
    fake.mode, fake.fail = "bound", failure
    outcome = run(instance)
    assert outcome["status"] == "STOPPED"
    assert outcome["http_attempts"] == http_count
    assert sum(a == failure for a, _ in fake.calls) <= 1
    assert fake.http.count(failure) <= 1
    assert (instance.work / "claim.json").exists()
    with pytest.raises(d.StopRun, match="already_exists"):
        run(instance)


def test_created_id_uncertain_only_known_resources_removed(session):
    instance, fake = session
    fake.fail = "create_sentinel"
    outcome = run(instance)
    assert outcome["unresolved_cleanup"] == {
        "sentinel": "unknown_id_manual_review", "responder": fake.ids["responder"],
        "net_responder": fake.ids["net_responder"], "net_sentinel": fake.ids["net_sentinel"],
    }
    assert outcome["docker_uncertainty"]["possibly_committed"] is True
    assert outcome["cleanup_docker"] == 0 and outcome["cleanup_owner_review"]
    assert all(fake.ids["sentinel"] not in args for _, args in fake.calls)


@pytest.mark.parametrize("field,value", [("Internal", False), ("EnableIPv6", True), ("Driver", "host")])
def test_network_mutations_fail_before_probes(session, field, value):
    instance, fake = session
    fake.mutate = lambda row: row.update({field: value}) if "Driver" in row else None
    outcome = run(instance)
    assert outcome["status"] == "STOPPED" and outcome["http_attempts"] == 0


def test_wildcard_refused_before_probes(session):
    instance, fake = session
    fake.mode = "wildcard"
    assert run(instance)["http_attempts"] == 0
    assert fake.http == []


def test_deadline_refuses_before_audit_or_claim(session):
    instance, fake = session
    instance.clock = lambda: instance.start + d.TOTAL
    with pytest.raises(d.StopRun, match="deadline"):
        run(instance)
    assert not fake.calls and not fake.http
    assert not instance.audit.exists() and not instance.work.exists()


def test_budgets_wrong_order_and_unauthorized_extra_calls(session):
    instance, fake = session
    instance.local_identity = fake.binding()
    with pytest.raises(d.StopRun, match="docker_action_requires_claim"):
        instance.docker("start_responder")
    with pytest.raises(d.StopRun, match="docker_order"):
        instance.docker("engine")
    with pytest.raises(d.StopRun, match="http_action_requires_claim"):
        instance.http("ubuntu_private_socket_http")
    instance.claimed = True
    with pytest.raises(d.StopRun, match="preflight_already_adopted"):
        instance.docker("context")
    with pytest.raises(d.StopRun, match="http_budget_or_order"):
        instance.http("ubuntu_private_socket_http")
    instance.normal, instance.cleanup_count, instance.http_count = 14, 6, 3
    with pytest.raises(d.StopRun, match="docker_budget"):
        instance.docker("cleanup_remove_", cleanup=True)
    with pytest.raises(d.StopRun, match="http_budget_or_order"):
        instance.http("inside_http")
    assert not fake.calls and not fake.http


def test_preservation_failure_stops_and_keeps_evidence(session):
    instance, _ = session
    def verify():
        if instance.claimed:
            raise d.StopRun("bad historical hash")
        return {}
    result = instance.run("a" * 40, verify)
    assert result["status"] == "STOPPED" and "bad historical hash" in result["preservation_failure"]
    assert (instance.work / "result.json").exists()


def test_reject_preexisting_path_even_without_claim(session):
    instance, fake = session
    instance.work.mkdir()
    with pytest.raises(d.StopRun, match="already_exists"):
        run(instance)
    assert not fake.calls


def test_plan_pinned_and_budgets_match_code():
    assert d.sha(d.PLAN) == d.PLAN_SHA
    original = json.loads(d.ORIGINAL_PLAN.read_bytes())
    assert d.sha(d.ORIGINAL_PLAN) == d.ORIGINAL_PLAN_SHA
    assert d.sha(d.PREVIOUS_PLAN) == d.PREVIOUS_PLAN_SHA
    assert d.sha(d.V3_PLAN) == d.V3_PLAN_SHA
    assert all(value == original[k] for k, value in json.loads(d.PLAN.read_bytes()).items()
               if k in original and k not in {"evidence", "run_id", "work"})
    plan = json.loads(d.PLAN.read_bytes())
    limits = plan["limits"]
    assert plan["run_id"] == d.RUN_ID and Path(plan["work"]) == d.WORK
    assert len(plan["docker_sequence"]) == len(d.ORDER) == limits["normal_docker_commands"]
    assert len(plan["http_sequence"]) == d.MAX_HTTP == limits["http_attempts"]
    assert limits["total_seconds"] == d.TOTAL and limits["docker_commands"] == d.MAX_DOCKER
    assert limits["normal_window_seconds"] == d.NORMAL_WINDOW
    for program in (d.RESPONDER, d.SENTINEL, d.INSIDE, d.CONTROL, d.EGRESS):
        compile(program, "<fictional program>", "exec")


@pytest.mark.parametrize(("problem", "count", "reason"), [
    ("context", 1, "unexpected_docker_context_or_endpoint"),
    ("endpoint", 1, "unexpected_docker_context_or_endpoint"),
    ("engine", 2, "unexpected_engine_identity"),
    ("labels", 3, "unexpected_image_identity"),
    ("image", 3, "unexpected_image_identity"),
    ("command", 2, "injected docker failure"),
    ("port", 3, "port unavailable"),
])
def test_preflight_failure_keeps_audit_and_does_not_claim(session, problem, count, reason):
    instance, fake = session
    if problem == "context":
        fake.context = "alternate"
    elif problem == "endpoint":
        fake.endpoint = "tcp://127.0.0.1:2375"
    elif problem == "engine":
        fake.engine = "different-engine"
    elif problem == "labels":
        fake.image_labels.pop("org.project5.context")
    elif problem == "image":
        fake.image = "different-image"
    elif problem == "command":
        fake.fail = "engine"
    else:
        fake.fail = "bound_port"
    outcome = run(instance)
    assert outcome["status"] == "STOPPED" and reason in outcome["failure"]
    assert outcome["normal_docker"] == len(fake.calls) == count
    assert not outcome["claim_created"] and not instance.work.exists()
    journal = instance.audit / "actions.jsonl"
    assert d.sha(journal) == outcome["journal_sha256"]
    last = json.loads(journal.read_bytes().splitlines()[-1])
    if problem == "command":
        assert last["event"] == "error" and last["action"] == "engine"
    with pytest.raises(d.StopRun, match="already_exists"):
        run(instance)
    assert len(fake.calls) == count


def test_pre_send_fsync_failure_never_dispatches(session, monkeypatch):
    instance, fake = session
    real = d.os.fsync
    def inject(fd):
        log = instance.audit / "actions.jsonl"
        if log.exists() and b'"event": "before"' in log.read_bytes():
            monkeypatch.setattr(d.os, "fsync", real)
            raise OSError("injected fsync failure")
        return real(fd)
    monkeypatch.setattr(d.os, "fsync", inject)
    outcome = run(instance)
    assert "fsync failure" in outcome["failure"] and not fake.calls
    assert outcome["normal_docker"] == outcome["normal_cli_launches"] == 0
    assert not instance.work.exists()
    assert (instance.audit / "result.json").exists()


def test_preexisting_audit_refused_without_overwrite(session):
    instance, fake = session
    instance.audit.mkdir()
    path = instance.audit / "actions.jsonl"
    path.write_bytes(b"preserved")
    with pytest.raises(d.StopRun, match="already_exists"):
        run(instance)
    assert path.read_bytes() == b"preserved" and not fake.calls


def test_concurrent_preflight_reservation_allows_only_one_sender(session):
    instance, fake = session
    other_fake = Fake(instance.work)
    other_fake.verify_socket = lambda: None
    other = d.Session(other_fake, instance.work)
    other.sync_directory = instance.sync_directory
    barrier = Barrier(2)
    for s in (instance, other):
        reserve = s.reserve_audit
        def simultaneous(reserve=reserve):
            barrier.wait(timeout=3)
            reserve("a" * 40)
        s.reserve_audit = lambda _, f=simultaneous: f()

    def attempt(s):
        try:
            return run(s)["status"]
        except d.StopRun as error:
            return str(error)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, (instance, other)))
    assert sorted(results) == ["DIAGNOSTIC_COMPLETE", "fixed_preflight_audit_already_exists"]
    assert sorted([len(fake.calls), len(other_fake.calls)]) == [0, 20]


def test_claim_race_does_not_touch_other_workspace(session):
    instance, fake = session
    def verify():
        instance.work.mkdir()
        (instance.work / "claim.json").write_bytes(b"other owner")
    result = instance.run("a" * 40, verify)
    assert "already_exists" in result["failure"]
    assert not result["claim_created"] and len(fake.calls) == 3
    assert (instance.work / "claim.json").read_bytes() == b"other owner"


def test_binding_change_during_preflight_refuses_claim(session):
    instance, fake = session
    def verify():
        fake.token = "switched-backend"
    result = instance.run("a" * 40, verify)
    assert "docker_local_identity_changed" in result["failure"]
    assert not instance.work.exists() and len(fake.calls) == 3


def test_binding_change_after_claim_stops_before_first_mutation(session, monkeypatch):
    instance, fake = session
    claim = instance.claim
    def switch(commit):
        claim(commit)
        fake.token = "switched-backend"
    monkeypatch.setattr(instance, "claim", switch)
    result = run(instance)
    assert result["claim_created"] and "docker_local_identity_changed" in result["failure"]
    assert len(fake.calls) == 3 and not fake.http


def test_preservation_failure_prevents_claim(session):
    instance, fake = session
    def verify():
        raise d.StopRun("bad historical hash")
    result = instance.run("a" * 40, verify)
    assert "bad historical hash" in result["failure"]
    assert not instance.work.exists() and len(fake.calls) == 3



@pytest.mark.parametrize("key", ["DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_CONFIG",
                                  "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH", "DOCKER_API_VERSION"])
def test_native_override_rejected_before_files_or_subprocess(tmp_path, monkeypatch, key):
    monkeypatch.setenv(key, "")
    monkeypatch.setattr(d.subprocess, "Popen", lambda *_, **__: pytest.fail("Docker forbidden in tests"))
    instance = d.Session(d.Transport(tmp_path / "fixture-config"), tmp_path / "synthetic")
    with pytest.raises(d.TargetUncertain, match="docker_environment_override_forbidden"):
        run(instance)
    assert not instance.audit.exists() and not instance.work.exists()


def test_native_rejects_wrong_absolute_link_target(tmp_path, monkeypatch):
    entry = tmp_path / "docker"
    info = SimpleNamespace(st_mode=stat.S_IFLNK, st_uid=0)
    real = d.Path.lstat
    monkeypatch.setattr(d, "CLI_ENTRY", entry)
    monkeypatch.setattr(d.Path, "lstat", lambda path: info if path == entry else real(path))
    monkeypatch.setattr(d.os, "readlink", lambda _: "/example/other-docker")
    with pytest.raises(d.TargetUncertain, match="unexpected_docker_cli_path"):
        d.Transport(tmp_path).binding()


@pytest.mark.parametrize("uid,gid,mode", [
    (1000, 0, 0o755), (0, 1000, 0o775), (0, 0, 0o777), (0, 0, 0o4755), (0, 0, 0o644),
])
def test_untrusted_linux_cli_file_rejected(uid, gid, mode):
    with pytest.raises(d.StopRun, match="untrusted_docker_cli_file"):
        d.validate_cli_file(SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid, st_gid=gid, st_size=1024))


@pytest.mark.parametrize("index,duplicate", [(1, True), (3, False)])
def test_preflight_receipts_must_be_exact_before_claim(session, index, duplicate):
    instance, fake = session
    def verify():
        path = instance.audit / "actions.jsonl"
        rows = path.read_bytes().splitlines(keepends=True)
        if duplicate:
            rows.insert(index, rows[index])
        else:
            del rows[index]
        path.write_bytes(b"".join(rows))
    result = instance.run("a" * 40, verify)
    assert "missing_duplicate_or_changed_preflight_receipt" in result["failure"]
    assert len(fake.calls) == 3 and not instance.work.exists()


def test_cleanup_uncertainty_halts_without_further_dispatch(session, monkeypatch):
    instance, fake = session
    original = fake.command
    def send(args, seconds, on_launch):
        reply = original(args, seconds, on_launch)
        if fake.calls[-1][0] == "cleanup_stop_responder":
            raise TimeoutError("lost stop response")
        return reply
    monkeypatch.setattr(fake, "command", send)
    outcome = run(instance)
    assert outcome["status"] == "STOPPED" and outcome["cleanup_docker"] == 3
    assert fake.calls[-1][0] == "cleanup_stop_responder"
    assert not any(a == "cleanup_remove_responder" for a, _ in fake.calls)
    assert outcome["docker_uncertainty"]["possibly_committed"] is True
    assert set(outcome["unresolved_cleanup"]) == {"responder", "net_responder", "net_sentinel"}


def test_deterministic_probe_mismatch_cleans_exact_owned_ids(session, monkeypatch):
    instance, fake = session
    monkeypatch.setattr(fake, "request", lambda *_: {"status": 503, "body": "fixture not ready"})
    result = run(instance)
    assert result["status"] == "STOPPED"
    assert result["docker_uncertainty"] is None and result["cleanup_docker"] == 6
    assert not result["unresolved_cleanup"]


def test_local_endpoint_drift_after_resource_creation_blocks_cleanup(session, monkeypatch):
    instance, fake = session
    original = fake.command
    def send(args, seconds, on_launch):
        value = original(args, seconds, on_launch)
        if fake.calls[-1][0] == "create_sentinel":
            fake.token = "different-local-socket"
        return value
    monkeypatch.setattr(fake, "command", send)
    result = run(instance)
    assert result["docker_uncertainty"]["action"] == "create_sentinel"
    assert result["docker_uncertainty"]["unconfirmed_created_id"] == fake.ids["sentinel"]
    assert result["docker_uncertainty"]["possibly_committed"] is True
    assert result["cleanup_docker"] == 0 and not fake.http
    assert result["unresolved_cleanup"]["net_sentinel"] == fake.ids["net_sentinel"]


@pytest.mark.parametrize("detectable", [True, False])
def test_unchanged_endpoint_substitution_limits(session, monkeypatch, detectable):
    instance, fake = session
    original = fake.command
    switched = False
    def send(args, seconds, on_launch):
        nonlocal switched
        if args[:2] == ["network", "create"]:
            switched = True
        return original(args, seconds, on_launch)
    if detectable:
        def changed_label(row):
            if switched and "Config" in row:
                row["Config"]["Labels"]["org.project5.diagnostic"] = "other"
        fake.mutate = changed_label
    monkeypatch.setattr(fake, "command", send)
    result = run(instance)
    assert switched
    if detectable:
        assert result["status"] == "STOPPED" and result["cleanup_docker"] == 0 and result["http_attempts"] == 0
        assert result["docker_uncertainty"]["action"] == "resource_inspection"
    else:
        # Indistinguishable local observations cannot identify a different upstream.
        assert result["status"] == "DIAGNOSTIC_COMPLETE" and result["normal_docker"] == 14



@pytest.fixture
def cli_paths(tmp_path, monkeypatch):
    audit = tmp_path.resolve() / "fixture-audit"
    audit.mkdir()
    transport = d.Transport(audit)
    transport.home.mkdir()
    transport.config.mkdir()
    real = d.Path.lstat
    overrides = {}

    def fake_stat(path):
        info = real(path)
        if path != audit and audit not in path.parents:
            return info
        mode = stat.S_IFMT(info.st_mode) | (0o700 if stat.S_ISDIR(info.st_mode) else 0o600)
        values = dict(st_dev=info.st_dev, st_ino=info.st_ino, st_size=info.st_size,
                      st_uid=10001, st_gid=10001, st_mode=mode, st_nlink=info.st_nlink)
        return SimpleNamespace(**(values | overrides.get(path, {})))

    monkeypatch.setattr(d.Path, "lstat", fake_stat)
    return transport, overrides


@pytest.mark.parametrize(("args", "routing"), [
    (("context", "inspect", "default"), ["--context", "default"]),
    (("info", "--format", "{{json .}}"), ["--host", d.ENDPOINT]),
    (("network", "create", "--internal", "fictional"), ["--host", d.ENDPOINT]),
])
def test_cli_exec_uses_separate_home_config_and_pinned_file(cli_paths, monkeypatch, args, routing):
    transport, _ = cli_paths
    inode = SimpleNamespace(st_ino=7, st_size=4096, st_dev=5, st_ctime_ns=19, st_uid=0, st_gid=0,
                            st_mode=stat.S_IFREG | 0o775)
    transport._cli_stamp = d.stamp(inode)
    monkeypatch.setattr(transport, "binding", lambda: ("fake",))
    fd = 123
    fds = []
    ruleset = 124
    child = lambda: None
    monkeypatch.setattr(d.os, "O_NOFOLLOW", 1, raising=False)
    monkeypatch.setattr(d.os, "open", lambda path, flags: fd if path == d.CLI_BINARY else pytest.fail("unapproved open"))
    monkeypatch.setattr(d.os, "fstat", lambda _: inode)
    monkeypatch.setattr(d.os, "close", fds.append)

    @contextmanager
    def fake_rules(home, identity):
        assert home == transport.home and identity == transport._directory_ids[2]
        yield ruleset, child

    monkeypatch.setattr(d, "cli_write_rules", fake_rules)
    calls = []
    def stop(argv, **kwargs):
        calls.append(argv)
        assert kwargs["executable"] == f"/proc/self/fd/{fd}" and kwargs["pass_fds"] == (fd, ruleset)
        assert kwargs["preexec_fn"] is child and kwargs["umask"] == 0o077
        assert kwargs["cwd"] == transport.home
        assert kwargs["env"] == {"LANG": "C.UTF-8", "PATH": "/usr/bin:/bin",
                                 "HOME": str(transport.home), "OTEL_SDK_DISABLED": "true"}
        raise OSError("synthetic dispatch boundary")
    monkeypatch.setattr(d.subprocess, "Popen", stop)
    with pytest.raises(OSError, match="synthetic dispatch boundary"):
        transport.command(list(args), 1, lambda: pytest.fail("not launched"))
    assert calls == [[str(d.CLI_BINARY), "--config", str(transport.config), *routing, *args]]
    assert fds == [fd]


def fake_desktop_log(home):
    log = home / d.HOME_LOG
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as handle:
        handle.write(b"synthetic Docker Desktop logging side effect\n")


def test_repeated_preflight_with_desktop_logging_keeps_config_empty(cli_paths, session, monkeypatch):
    instance, fake = session
    transport, _ = cli_paths
    command = fake.command
    def desktop(args, seconds, launched):
        transport.guard_paths()
        reply = command(args, seconds, launched)
        fake_desktop_log(transport.home)
        transport.guard_paths()
        return reply
    monkeypatch.setattr(fake, "command", desktop)
    outcome = run(instance)
    assert outcome["status"] == "DIAGNOSTIC_COMPLETE" and len(fake.calls) == 20
    assert outcome["normal_docker"] == outcome["normal_cli_launches"] == 14
    assert (transport.home / d.HOME_LOG).read_bytes().count(b"logging side effect") == 20
    assert list(transport.config.iterdir()) == []


@pytest.mark.parametrize("after_launch", [False, True])
def test_config_write_stops_with_separate_slot_and_launch_counts(cli_paths, session, monkeypatch, after_launch):
    instance, fake = session
    transport, _ = cli_paths
    command = fake.command
    def desktop(args, seconds, launched):
        image = args[:2] == ["image", "inspect"]
        if image and not after_launch:
            (transport.config / "unexpected").touch()
        transport.guard_paths()
        response = command(args, seconds, launched)
        if image and after_launch:
            (transport.config / "unexpected").touch()
        transport.guard_paths()
        return response
    monkeypatch.setattr(fake, "command", desktop)
    outcome = run(instance)
    assert outcome["status"] == "STOPPED" and "docker_config_not_empty" in outcome["failure"]
    assert outcome["normal_docker"] == 3 and outcome["normal_cli_launches"] == (3 if after_launch else 2)
    assert outcome["docker_uncertainty"]["launch_confirmed"] is after_launch
    assert not instance.work.exists() and outcome["http_attempts"] == 0


@pytest.mark.parametrize("attribute,changes,reason", [
    ("home", {"st_uid": 1000}, "not_private"),
    ("home", {"st_mode": stat.S_IFDIR | 0o755}, "not_private"),
    ("home", {"st_ino": -1}, "identity_changed"),
    ("home", {"st_mode": stat.S_IFLNK | 0o777}, "not_private"),
    ("config", {"st_mode": stat.S_IFLNK | 0o777}, "not_private"),
])
def test_unsafe_cli_paths_fail_closed(cli_paths, attribute, changes, reason):
    transport, overrides = cli_paths
    transport.guard_paths()
    overrides[getattr(transport, attribute)] = changes
    with pytest.raises(d.StopRun, match=reason):
        transport.guard_paths()


@pytest.mark.parametrize("changes", [
    {"st_mode": stat.S_IFLNK | 0o600}, {"st_nlink": 2},
    {"st_mode": stat.S_IFREG | 0o666}, {"st_size": 1_000_001},
])
def test_symlink_hardlink_writable_or_unbounded_log_rejected(cli_paths, changes):
    transport, overrides = cli_paths
    fake_desktop_log(transport.home)
    overrides[transport.home / d.HOME_LOG] = changes
    with pytest.raises(d.StopRun, match="docker_cli"):
        transport.guard_paths()


def test_unknown_home_write_rejected(cli_paths):
    transport, _ = cli_paths
    (transport.home / "unexpected").touch()
    with pytest.raises(d.StopRun, match="unexpected_docker_cli_home_entry"):
        transport.guard_paths()


def test_landlock_blocks_outside_writes_in_benign_fixture_process(tmp_path):
    if sys.platform != "linux":
        pytest.skip("Linux Landlock only")
    try:
        d.landlock_kernel()
    except d.StopRun as error:
        pytest.skip(str(error))
    home, outside = tmp_path / "home", tmp_path / "outside"
    home.mkdir()
    outside.mkdir()
    regular = outside / "existing"
    regular.write_bytes(b"unchanged")
    payload = """
import sys
from pathlib import Path
home, outside = map(Path, sys.argv[1:])
(home/'allowed').write_text('fictional log')
for candidate in (outside/'escape', outside/'existing'):
    try:
        candidate.write_text('should not be written')
    except PermissionError:
        continue
    raise SystemExit(2)
"""
    with d.cli_write_rules(home, d.directory_identity(home.stat())) as (rules, child):
        result = subprocess.run([sys.executable, "-I", "-B", "-c", payload, str(home), str(outside)],
                                pass_fds=(rules,), preexec_fn=child, timeout=8)
    assert result.returncode == 0
    assert (home / "allowed").read_text() == "fictional log"
    assert not (outside / "escape").exists() and regular.read_bytes() == b"unchanged"


@pytest.mark.parametrize("duplicate", [False, True])
def test_launch_receipt_is_mandatory_and_never_counted_twice(session, monkeypatch, duplicate):
    instance, fake = session
    def malformed(args, seconds, launched):
        if duplicate:
            launched()
            launched()
        return "unexpected return without launch"
    monkeypatch.setattr(fake, "command", malformed)
    outcome = run(instance)
    reason = "duplicate_cli_launch_receipt" if duplicate else "missing_cli_launch_receipt"
    assert reason in outcome["failure"] and outcome["normal_docker"] == 1
    assert outcome["normal_cli_launches"] == int(duplicate)
    assert not instance.work.exists() and outcome["cleanup_docker"] == 0
