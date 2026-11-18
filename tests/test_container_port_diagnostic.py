import copy
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

    def event(self, transport):
        events = [json.loads(line) for line in (self.work / "actions.jsonl").read_text().splitlines()]
        assert events[-1]["event"] == "before" and events[-1]["transport"] == transport
        return events[-1]["action"]

    def command(self, args, seconds):
        action = self.event("docker")
        assert 0 < seconds <= 6
        self.calls.append((action, args))
        if action == self.fail:
            raise d.StopRun("injected docker failure")
        if action == "context":
            return json.dumps([{"Endpoints": {"docker": {"Host": "unix:///var/run/docker.sock"}}}])
        if action == "engine":
            return json.dumps({"ID": d.ENGINE, "OSType": "linux", "Architecture": "x86_64",
                               "DefaultRuntime": "runc", "SecurityOptions": ["name=seccomp,profile=builtin"],
                               "CgroupVersion": "2", "MemoryLimit": True, "PidsLimit": True,
                               "CpuCfsQuota": True, "CpuCfsPeriod": True})
        if action == "image":
            return json.dumps([{"Id": d.IMAGE, "Os": "linux", "Architecture": "amd64",
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
    with pytest.raises(d.StopRun, match="already_exists"):
        run(instance)
    assert len(fake.calls) == 20


@pytest.mark.parametrize(("failure", "http_count"), [
    ("bound_port", 0), ("start_responder", 0), ("listener", 0), ("inside_http", 1),
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


def test_deadline_preserves_claim_and_does_not_invoke_transport(session):
    instance, fake = session
    instance.clock = lambda: instance.start + d.TOTAL
    outcome = run(instance)
    assert outcome["status"] == "STOPPED" and "deadline" in outcome["failure"]
    assert not fake.calls and not fake.http


def test_budgets_wrong_order_and_unauthorized_extra_calls(session):
    instance, fake = session
    instance.claim("a" * 40)
    with pytest.raises(d.StopRun, match="docker_order"):
        instance.docker("start_responder")
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
        raise d.StopRun("bad historical hash")
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
    plan = json.loads(d.PLAN.read_bytes())
    limits = plan["limits"]
    assert plan["run_id"] == d.RUN_ID and Path(plan["work"]) == d.WORK
    assert len(plan["docker_sequence"]) == len(d.ORDER) == limits["normal_docker_commands"]
    assert len(plan["http_sequence"]) == d.MAX_HTTP == limits["http_attempts"]
    assert limits["total_seconds"] == d.TOTAL and limits["docker_commands"] == d.MAX_DOCKER
    assert limits["normal_window_seconds"] == d.NORMAL_WINDOW
    for program in (d.RESPONDER, d.SENTINEL, d.INSIDE, d.CONTROL, d.EGRESS):
        compile(program, "<fictional program>", "exec")
