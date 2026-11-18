import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Barrier
import json
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
        self.engine, self.image, self.token = d.ENGINE, d.IMAGE, "attested-fake"
        self.audit = work.with_name(work.name + ".preflight")

    def binding(self):
        return self.token

    def event(self, transport):
        directory = self.work if self.work.exists() else self.audit
        events = [json.loads(line) for line in (directory / "actions.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "before" and events[-1]["transport"] == transport
        return events[-1]["action"]

    def command(self, args, seconds):
        action = self.event("docker")
        assert 0 < seconds <= 6
        self.calls.append((action, args))
        if action in d.PREFLIGHT:
            assert not self.work.exists() and (self.audit / "intent.json").is_file()
        else:
            assert (self.work / "claim.json").is_file()
        if action == self.fail:
            raise d.StopRun("injected docker failure")
        if action == "context":
            return json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}])
        if action == "engine":
            return json.dumps({"ID": self.engine, "OSType": "linux", "Architecture": "x86_64",
                               "DefaultRuntime": "runc", "SecurityOptions": ["name=seccomp,profile=builtin"],
                               "CgroupVersion": "2", "MemoryLimit": True, "PidsLimit": True,
                               "CpuCfsQuota": True, "CpuCfsPeriod": True})
        if action == "image":
            return json.dumps([{"Id": self.image, "Os": "linux", "Architecture": "amd64",
                                "Config": {"User": "10001:10001", "Labels": d.IMAGE_LABELS,
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
    assert outcome["normal_docker"] == 14 and outcome["cleanup_docker"] == 6
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
    assert outcome["unresolved_cleanup"] == {"sentinel": "unknown_id_manual_review"}
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
    instance.bound_engine = fake.binding()
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
    assert all(plan == original[k] for k, plan in json.loads(d.PLAN.read_bytes()).items()
               if k in original and k != "evidence")
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
    ("engine", 2, "unexpected_engine_identity"),
    ("image", 3, "unexpected_image_identity"),
    ("command", 2, "injected docker failure"),
    ("port", 3, "port unavailable"),
])
def test_preflight_failure_keeps_audit_and_does_not_claim(session, problem, count, reason):
    instance, fake = session
    if problem == "engine":
        fake.engine = "different-engine"
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
    assert "fsync failure" in outcome["failure"] and not fake.calls and outcome["normal_docker"] == 0
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
    assert "docker_engine_binding_changed" in result["failure"]
    assert not instance.work.exists() and len(fake.calls) == 3


def test_binding_change_after_claim_stops_before_first_mutation(session, monkeypatch):
    instance, fake = session
    claim = instance.claim
    def switch(commit):
        claim(commit)
        fake.token = "switched-backend"
    monkeypatch.setattr(instance, "claim", switch)
    result = run(instance)
    assert result["claim_created"] and "docker_engine_binding_changed" in result["failure"]
    assert len(fake.calls) == 3 and not fake.http


def test_preservation_failure_prevents_claim(session):
    instance, fake = session
    def verify():
        raise d.StopRun("bad historical hash")
    result = instance.run("a" * 40, verify)
    assert "bad historical hash" in result["failure"]
    assert not instance.work.exists() and len(fake.calls) == 3


def test_native_transport_blocks_before_audit_or_docker(tmp_path, monkeypatch):
    transport = d.Transport()
    monkeypatch.setattr(transport, "command", lambda *_: pytest.fail("native Docker must not run"))
    instance = d.Session(transport, tmp_path / "synthetic")
    with pytest.raises(d.StopRun, match="desktop_engine_continuity_unattested"):
        run(instance)
    assert not instance.audit.exists() and not instance.work.exists()
