"""Offline preparation for a separate, synthetic ingress diagnostic."""
import argparse
from contextlib import contextmanager
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import signal
import socket
import stat
import subprocess
import sys
import time

from evaluation.container_runtime import (
    IMAGE_ENVIRONMENTS, Native, ROOT, git, host_gate, preservation,
)
from evaluation.lifecycle_live import Journal, StopRun, save_new, sha

BASELINE = "6923dbdeb1e7d89637ea1daac188f2f2e451895c"
ARCHIVE = ROOT / "reports/container-runtime-5b-once-consumed"
ARCHIVE_CHECKSUMS = "217da0328f7361b5e81e7cdfc6d13efa0bc59d0d34b9d02e6c34652474b1d050"
RUN_ID = "container-port-diag-5b-once"
WORK = Path("/var/tmp/project5-port-diag-5b-once")
PLAN = ROOT / "reports/container-port-diagnostic-preparation/plan.json"
PLAN_SHA = "fd44d2a2f8a2400d8388f1df0cdd1a460ab47d86b3b1a08b4b958a9a393bae74"
ENGINE = "d86d0c2c-42a0-4420-9f0d-0027845314bf"
IMAGE = "sha256:375ed722706d2bc40338803d48e15df32c3eade270ace39e23166d5e045f7b6f"
IMAGE_LABELS = {
    "org.project5.source": "f267a6dbfd3a67aae1e8285e8cca3e05897b1539",
    "org.project5.context": "3691f6470f429d194e5b21eaffc75dc1e9df659141fb1d75dfb2c1c43de7d91a",
}
LABEL = "org.project5.diagnostic=" + RUN_ID
TAG = "container-port-diagnostic"
PORT, LOCAL_PORT, SENTINEL_PORT = 18080, 45173, 18082
KEY = f"{PORT}/tcp"
HOST = f"127.0.0.1:{LOCAL_PORT}"
BODY = b"project5-fictional-ingress-diagnostic\n"
EXPECTED = {"status": 200, "body": BODY.decode()}
TOTAL, NORMAL_WINDOW, MAX_DOCKER, MAX_HTTP = 180, 110, 20, 3
ORDER = (
    "context", "engine", "image", "network_responder", "network_sentinel",
    "create_responder", "create_sentinel", "start_responder", "start_sentinel",
    "inspect_containers", "inspect_networks", "inside_http", "sentinel_control", "blocked_egress",
)
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")
HTTP_ORDER = ("inside_http", "ubuntu_published_http", "ubuntu_private_socket_http")
CHANGED = {
    ".github/workflows/runtime-preparation.yml", "evaluation/container_port_diagnostic.py",
    "tests/test_container_port_diagnostic.py", "docs/container-port-diagnostic.md",
    "reports/container-port-diagnostic-preparation/plan.json",
    "reports/container-port-diagnostic-preparation/offline.md",
}


def sole_target(port, local_port):
    return {f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(local_port)}]}


def require_requested(item, port, local_port):
    try:
        if item["HostConfig"]["PortBindings"] == sole_target(port, local_port):
            return
    except (KeyError, TypeError):
        pass
    raise StopRun("unexpected_requested_mapping")


def require_effective(item, port, local_port):
    require_requested(item, port, local_port)
    try:
        if item["NetworkSettings"]["Ports"] == sole_target(port, local_port):
            return
    except (KeyError, TypeError):
        pass
    raise StopRun("ineffective_or_unexpected_port_mapping")


def publication(item):
    """Recognize the archival empty result; never authorize HTTP on it."""
    require_requested(item, PORT, LOCAL_PORT)
    ports = item.get("NetworkSettings", {}).get("Ports")
    if ports in ({KEY: []}, {KEY: None}):
        return "requested_but_not_effective"
    require_effective(item, PORT, LOCAL_PORT)
    return "effective_exact_loopback"


# The responder shares no source, data or model mount. It only serves a fixed
# GET from two sockets. The sentinel speaks a fixed non-HTTP TCP token.
RESPONDER = r'''
import http.server, os, socketserver, threading
BODY = b"project5-fictional-ingress-diagnostic\n"
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/fictional-health" or self.headers.get_all("Host") != ["127.0.0.1:45173"]:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)
    def log_message(self, *_): pass
os.umask(0o077)
with http.server.HTTPServer(("0.0.0.0", 18080), Handler) as tcp, socketserver.UnixStreamServer("/ingress/responder.sock", Handler) as uds:
    threading.Thread(target=tcp.serve_forever, daemon=True).start()
    uds.serve_forever()
'''
SENTINEL = r'''
import socketserver
class Handler(socketserver.BaseRequestHandler):
    def handle(self): self.request.sendall(b"fictional-sentinel")
with socketserver.TCPServer(("0.0.0.0", 18082), Handler) as server:
    server.serve_forever()
'''
INSIDE = r'''
import http.client, json
with_connection = http.client.HTTPConnection("127.0.0.1", 18080, timeout=2)
with_connection.request("GET", "/fictional-health", headers={"Host":"127.0.0.1:45173"})
response = with_connection.getresponse()
print(json.dumps({"status":response.status,"body":response.read(129).decode()}))
'''
CONTROL = r'''
import socket
with socket.create_connection(("127.0.0.1",18082), timeout=2) as client:
    if client.makefile("rb").read(19) != b"fictional-sentinel": raise SystemExit(1)
print("sentinel_listening")
'''
EGRESS = r'''
import errno, socket, sys
with open("/proc/net/route") as routes:
    if any(line.split()[1] == "00000000" for line in list(routes)[1:]): raise SystemExit("default_ipv4_route")
with open("/proc/net/if_inet6") as interfaces:
    if any(line.split()[0] != "00000000000000000000000000000001" for line in interfaces):
        raise SystemExit("non_loopback_ipv6")
try:
    with socket.create_connection((sys.argv[1],18082), timeout=2): pass
except socket.timeout:
    print("cross_bridge_blocked")
except OSError as error:
    if error.errno not in (errno.ENETUNREACH,errno.EHOSTUNREACH,errno.EACCES,errno.EPERM): raise
    print("cross_bridge_blocked")
else: raise SystemExit("cross_bridge_reachable")
'''


def require(condition, message):
    if not condition:
        raise StopRun(message)


def exact_id(value):
    value = value.strip()
    require(re.fullmatch("[0-9a-f]{64}", value), "uncertain_created_resource_id")
    return value


def one(value):
    require(isinstance(value, list) and len(value) == 1, "unexpected_inspect_length")
    return value[0]


@contextmanager
def alarm(seconds):
    def expired(*_):
        raise StopRun("overall_or_operation_timeout")
    previous = signal.signal(signal.SIGALRM, expired)
    outer, interval = signal.getitimer(signal.ITIMER_REAL)
    start = time.monotonic()
    signal.setitimer(signal.ITIMER_REAL, min(seconds, outer) if outer else seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if outer:
            signal.setitimer(signal.ITIMER_REAL, max(.001, outer - (time.monotonic() - start)), interval)


class Transport(Native):
    def verify_socket(self):
        s = Path("/var/run/docker.sock").lstat()
        require(stat.S_ISSOCK(s.st_mode) and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o660,
                "unexpected_docker_socket")

    def wait_listener(self, path, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                s = path.lstat()
            except FileNotFoundError:
                time.sleep(.05)
                continue
            require(stat.S_ISSOCK(s.st_mode) and (s.st_uid, s.st_gid, stat.S_IMODE(s.st_mode))
                    == (10001, 10001, 0o700), "private_socket_permissions")
            return
        raise StopRun("listener_not_ready")

    def precheck_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", LOCAL_PORT))

    def request(self, action, path, seconds):
        connection = http.client.HTTPConnection("127.0.0.1", LOCAL_PORT, timeout=seconds)
        if action == "ubuntu_private_socket_http":
            self.wait_listener(path, min(seconds, .10))

            def connect():
                connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                connection.sock.settimeout(seconds)
                connection.sock.connect(os.fspath(path))
            connection.connect = connect
        with alarm(seconds):
            try:
                connection.request("GET", "/fictional-health", headers={"Host": HOST})
                reply = connection.getresponse()
                return {"status": reply.status, "body": reply.read(129).decode()}
            finally:
                connection.close()


class Session:
    def __init__(self, transport, work, clock=time.monotonic, started=None):
        self.transport, self.work, self.clock = transport, work, clock
        self.start = clock() if started is None else started
        self.normal = self.cleanup_count = self.http_count = 0
        self.ids, self.uncertain, self.results = {}, set(), {}
        self.journal = None

    def remaining(self, cleanup=False):
        seconds = self.start + (TOTAL - 12 if cleanup else NORMAL_WINDOW) - self.clock()
        require(seconds >= .1, "diagnostic_deadline")
        return seconds

    def receipt(self, name, data):
        self.journal.write({"event": "result", "action": name, "result": data})

    def docker(self, action, *argv, cleanup=False):
        limit = min(6, self.remaining(cleanup))
        require(self.normal + self.cleanup_count < MAX_DOCKER, "docker_budget")
        if cleanup:
            require(self.cleanup_count < 6 and action.startswith("cleanup_"), "cleanup_budget")
            self.cleanup_count += 1
        else:
            require(self.normal < len(ORDER) and ORDER[self.normal] == action, "docker_order")
            self.normal += 1
        self.journal.write({"event": "before", "transport": "docker", "action": action, "argv": argv})
        result = self.transport.command(list(argv), limit)
        self.receipt(action, result)
        return result

    def http(self, action):
        expected = HTTP_ORDER if self.results.get("publication") == "effective_exact_loopback" else (HTTP_ORDER[0], HTTP_ORDER[2])
        limit = min(3, self.remaining())
        require(self.http_count < MAX_HTTP and self.http_count < len(expected)
                and expected[self.http_count] == action, "http_budget_or_order")
        self.http_count += 1
        self.journal.write({"event": "before", "transport": "http", "action": action,
                            "method": "GET", "path": "/fictional-health"})
        if action == "inside_http":
            value = json.loads(self.exe("inside_http", "responder", INSIDE))
        else:
            value = self.transport.request(action, self.work / "ingress/responder.sock", limit)
        self.receipt(action, value)
        self.results[action] = value
        return value

    def exe(self, action, role, source, *args):
        return self.docker(action, "exec", "--user", "10001:10001", self.ids[role],
                           "/usr/local/bin/python", "-I", "-B", "-S", "-c", source, *args)

    def claim(self, commit):
        try:
            self.work.mkdir(mode=0o700)
        except FileExistsError as error:
            raise StopRun("fixed_workspace_or_claim_already_exists") from error
        # A directory without a complete claim is also permanently used.
        self.sync_directory(self.work.parent)
        save_new(self.work / "claim.json", {"run_id": RUN_ID, "source": commit, "plan_sha256": PLAN_SHA})
        self.journal = Journal(self.work / "actions.jsonl")
        (self.work / "ingress").mkdir(mode=0o700)
        self.sync_directory(self.work)

    @staticmethod
    def sync_directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def create(self, role, action, *argv):
        self.uncertain.add(role)
        ident = exact_id(self.docker(action, *argv))
        require(ident not in self.ids.values(), "duplicate_created_resource_id")
        self.ids[role] = ident
        self.uncertain.remove(role)

    def preflight(self):
        contexts = json.loads(self.docker("context", "context", "inspect", "default"))
        require(one(contexts)["Endpoints"]["docker"]["Host"] == "unix:///var/run/docker.sock", "unexpected_docker_endpoint")
        self.transport.verify_socket()
        info = json.loads(self.docker("engine", "info", "--format", "{{json .}}"))
        require((info["ID"], info["OSType"], info["Architecture"], info["DefaultRuntime"])
                == (ENGINE, "linux", "x86_64", "runc"), "unexpected_engine_identity")
        security = info["SecurityOptions"]
        require(any("name=seccomp" in x and "profile=builtin" in x for x in security)
                and not any("name=rootless" in x or "name=userns" in x for x in security)
                and info["CgroupVersion"] == "2" and all(info[k] is True for k in
                ("MemoryLimit", "PidsLimit", "CpuCfsQuota", "CpuCfsPeriod")),
                "unexpected_engine_security")
        image = one(json.loads(self.docker("image", "image", "inspect", IMAGE)))
        cfg = image["Config"]
        require((image["Id"], image["Os"], image["Architecture"], cfg["User"])
                == (IMAGE, "linux", "amd64", "10001:10001") and not cfg.get("Volumes")
                and not cfg.get("OnBuild") and all(cfg["Labels"].get(k) == v for k, v in IMAGE_LABELS.items())
                and sorted(cfg["Env"]) == sorted(f"{k}={v}" for k, v in IMAGE_ENVIRONMENTS["backend"].items()),
                "unexpected_image_identity")
        self.transport.precheck_port()

    def construct(self):
        no_proxy = [part for key in PROXY_ENV for part in ("--env", key + "=")]
        for role in ("responder", "sentinel"):
            self.create("net_" + role, "network_" + role, "network", "create",
                        "--driver", "bridge", "--internal", "--ipv6=false", "--label", LABEL, TAG + "-" + role)
        for role, source in (("responder", RESPONDER), ("sentinel", SENTINEL)):
            extra = (["--publish", f"127.0.0.1:{LOCAL_PORT}:{PORT}/tcp", "--mount",
                      f"type=bind,source={self.work}/ingress,target=/ingress"] if role == "responder" else [])
            self.create(role, "create_" + role, "create", "--name", TAG + "-" + role,
                        "--label", LABEL, "--network", self.ids["net_" + role], "--pull", "never",
                        "--user", "10001:10001", "--read-only", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges:true", "--cpus", ".5", "--memory", "128m",
                        "--memory-swap", "128m", "--pids-limit", "24", "--restart", "no",
                        "--no-healthcheck", "--entrypoint", "/usr/local/bin/python", *no_proxy, *extra,
                        IMAGE, "-I", "-B", "-S", "-c", source)
        for role in ("responder", "sentinel"):
            self.docker("start_" + role, "start", self.ids[role])

    def inspect(self):
        items = json.loads(self.docker("inspect_containers", "container", "inspect",
                                       self.ids["responder"], self.ids["sentinel"]))
        require(isinstance(items, list) and len(items) == 2, "unexpected_container_count")
        nets = json.loads(self.docker("inspect_networks", "network", "inspect", self.ids["net_responder"], self.ids["net_sentinel"]))
        require(isinstance(nets, list) and len(nets) == 2, "unexpected_network_count")
        addresses, subnets = {}, {}
        for role, item, net in zip(("responder", "sentinel"), items, nets, strict=True):
            host, cfg = item["HostConfig"], item["Config"]
            name = TAG + "-" + role
            network_id, container_id = self.ids["net_" + role], self.ids[role]
            require(net["Id"] == network_id and net["Name"] == name and net["Driver"] == "bridge"
                    and net["Internal"] is True and net["EnableIPv6"] is False
                    and net["Labels"].get("org.project5.diagnostic") == RUN_ID
                    and set(net["Containers"]) == {container_id}, "unexpected_network_topology")
            ns = item["NetworkSettings"]["Networks"]
            require(set(ns) == {name} and ns[name]["NetworkID"] == network_id
                    and ns[name]["Gateway"] == "" and ns[name]["IPv6Gateway"] == "", "unexpected_container_routes")
            address = ipaddress.IPv4Address(ns[name]["IPAddress"])
            ipam = one(net["IPAM"]["Config"])
            subnet = ipaddress.IPv4Network(ipam["Subnet"])
            require(subnet.subnet_of(ipaddress.IPv4Network("172.16.0.0/12")) or
                    subnet.subnet_of(ipaddress.IPv4Network("192.168.0.0/16")) or
                    subnet.subnet_of(ipaddress.IPv4Network("10.0.0.0/8")), "non_private_test_network")
            require(address in subnet and address != subnet.network_address and address != subnet.broadcast_address
                    and ipaddress.IPv4Interface(net["Containers"][container_id]["IPv4Address"]).ip == address,
                    "unexpected_container_address")
            addresses[role], subnets[role] = str(address), subnet
            require(item["Id"] == container_id and item["Name"] == "/" + name and item["Image"] == IMAGE
                    and item["State"]["Running"] is True and cfg["User"] == "10001:10001"
                    and cfg["Labels"].get("org.project5.diagnostic") == RUN_ID
                    and sorted(cfg["Env"]) == sorted([f"{k}={v}" for k,v in IMAGE_ENVIRONMENTS["backend"].items()]
                                                      + [f"{k}=" for k in PROXY_ENV])
                    and cfg["Entrypoint"] == ["/usr/local/bin/python"]
                    and cfg["Cmd"] == ["-I", "-B", "-S", "-c", RESPONDER if role == "responder" else SENTINEL]
                    and host["NetworkMode"] in (name, network_id)
                    and host["Privileged"] is False and host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"]
                    and not host.get("CapAdd") and not host.get("ExtraHosts")
                    and not host.get("Devices") and not host.get("DeviceRequests")
                    and host["SecurityOpt"] == ["no-new-privileges:true"] and host["PidsLimit"] == 24
                    and host["NanoCpus"] == 500_000_000 and host["Memory"] == host["MemorySwap"] == 134217728
                    and host["RestartPolicy"] == {"Name": "no", "MaximumRetryCount": 0},
                    "unexpected_container_security")
            if role == "responder":
                mount = one(item["Mounts"])
                require((mount["Type"], mount["Source"], mount["Destination"], mount["RW"])
                        == ("bind", str(self.work / "ingress"), "/ingress", True)
                        and mount["Propagation"] == "rprivate", "unexpected_responder_mount")
                self.results["publication"] = publication(item)
            else:
                require(not item["Mounts"] and not host.get("PortBindings")
                        and item["NetworkSettings"]["Ports"] in ({}, None), "unexpected_sentinel_exposure")
        require(not subnets["responder"].overlaps(subnets["sentinel"]), "test_subnets_overlap")
        return addresses["sentinel"]

    def exercise(self):
        self.preflight()
        self.construct()
        target = self.inspect()
        self.transport.wait_listener(self.work / "ingress/responder.sock", min(5, self.remaining()))
        require(self.http("inside_http") == EXPECTED, "listener_http_failed")
        require(self.exe("sentinel_control", "sentinel", CONTROL).strip() == "sentinel_listening", "sentinel_control_failed")
        require(self.exe("blocked_egress", "responder", EGRESS, target).strip() == "cross_bridge_blocked", "egress_test_failed")
        self.results["cross_bridge_tcp"] = "denied_with_positive_control"
        if self.results["publication"] == "effective_exact_loopback":
            try:
                published = self.http("ubuntu_published_http")
            except ConnectionRefusedError as error:
                raise StopRun("effective_mapping_but_ubuntu_connection_refused") from error
            require(published == EXPECTED, "ubuntu_publication_response_mismatch")
        else:
            self.results["ubuntu_published_http"] = "skipped_no_effective_mapping"
        require(self.http("ubuntu_private_socket_http") == EXPECTED, "private_socket_response_mismatch")

    def clean(self):
        unresolved = dict.fromkeys(self.uncertain, "unknown_id_manual_review")
        for role in ("sentinel", "responder"):
            if role not in self.ids:
                continue
            ident = self.ids[role]
            try:
                self.docker("cleanup_stop_" + role, "stop", "--time", "2", ident, cleanup=True)
            except Exception:
                unresolved[role + "_stop"] = ident
            try:
                self.docker("cleanup_remove_" + role, "rm", ident, cleanup=True)
            except Exception:
                unresolved[role] = ident
        for role in ("net_sentinel", "net_responder"):
            if role in self.ids:
                try:
                    self.docker("cleanup_remove_" + role, "network", "rm", self.ids[role], cleanup=True)
                except Exception:
                    unresolved[role] = self.ids[role]
        return unresolved

    def run(self, commit, verify):
        self.claim(commit)
        outcome = {"status": "STOPPED", "run_id": RUN_ID, "source": commit, "plan_sha256": PLAN_SHA}
        try:
            self.exercise()
            outcome["status"] = "DIAGNOSTIC_COMPLETE"
        except (Exception, KeyboardInterrupt) as error:
            outcome["failure"] = f"{type(error).__name__}: {error}"
            if hasattr(error, "details"):
                self.receipt("docker_failure", error.details)
        finally:
            outcome["unresolved_cleanup"] = self.clean()
            if outcome["unresolved_cleanup"]:
                outcome["status"] = "STOPPED"
            try:
                outcome["preservation"] = verify()
            except (Exception, KeyboardInterrupt) as error:
                outcome["status"] = "STOPPED"
                outcome["preservation_failure"] = f"{type(error).__name__}: {error}"
            outcome.update(results=self.results, captured_ids=self.ids, normal_docker=self.normal,
                           cleanup_docker=self.cleanup_count, http_attempts=self.http_count,
                           actual_model_requests=0, windows_localhost="not_tested",
                           journal_sha256=sha(self.work / "actions.jsonl"))
            save_new(self.work / "result.json", outcome)
            self.sync_directory(self.work)
        return outcome


def verify_source(commit):
    require(re.fullmatch("[0-9a-f]{40}", commit) and git("rev-parse", "HEAD").decode().strip() == commit,
            "unapproved_source")
    require(not git("status", "--porcelain", "--untracked-files=all"), "dirty_checkout")
    require(sha(PLAN) == PLAN_SHA, "frozen_plan_changed")
    diff = set(git("diff", "--name-only", "--no-renames", BASELINE, commit).decode().splitlines())
    require(diff == CHANGED, "unexpected_changes_from_historical_delivery")
    result = preservation(require_local=True)
    require(len(result["checked"]) == 397, "historical_preservation_count")
    archive_manifest = ARCHIVE / "checksums.json"
    require(sha(archive_manifest) == ARCHIVE_CHECKSUMS, "historical_archive_manifest_changed")
    checksums = json.loads(archive_manifest.read_bytes())
    require(len(checksums) == 31 and all(sha(ARCHIVE / name) == digest for name, digest in checksums.items()),
            "historical_archive_changed")
    return {**result, "archive_checksums": checksums}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-authorized-once", metavar="APPROVED_SOURCE_SHA", required=True)
    options = parser.parse_args()
    commit = options.execute_authorized_once
    started = time.monotonic()
    try:
        if not sys.platform.startswith("linux"):
            raise StopRun("linux_host_required")
        with alarm(20):
            host_gate()
            require(not WORK.exists() and not WORK.is_symlink(), "fixed_workspace_or_claim_already_exists")
            verify_source(commit)
        session = Session(Transport(), WORK, started=started)
        with alarm(max(.1, TOTAL - (time.monotonic() - started))):
            result = session.run(commit, lambda: verify_source(commit))
        print(json.dumps({"status": result["status"], "result": str(WORK / "result.json")}))
        return int(result["status"] != "DIAGNOSTIC_COMPLETE")
    except (StopRun, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED_OR_STOPPED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
