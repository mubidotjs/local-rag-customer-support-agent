"""Offline preparation for a separate, synthetic ingress diagnostic."""
import argparse
import ctypes
import hashlib
from contextlib import contextmanager
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import subprocess
import sys
import threading
import time

from evaluation.container_runtime import (
    IMAGE_ENVIRONMENTS, ROOT, git, host_gate, preservation,
)
from evaluation.lifecycle_live import Journal, StopRun, save_new, sha

ARCHIVE = ROOT / "reports/container-runtime-5b-once-consumed"
ARCHIVE_CHECKSUMS = "217da0328f7361b5e81e7cdfc6d13efa0bc59d0d34b9d02e6c34652474b1d050"
RUN_ID = "container-port-diag-5b-v4-once"
WORK = Path("/var/tmp/project5-port-diag-5b-v4-once")
ORIGINAL_PLAN = ROOT / "reports/container-port-diagnostic-preparation/plan.json"
ORIGINAL_PLAN_SHA = "fd44d2a2f8a2400d8388f1df0cdd1a460ab47d86b3b1a08b4b958a9a393bae74"
PREVIOUS_PLAN = ORIGINAL_PLAN.with_name("plan-preclaim-v2.json")
PREVIOUS_PLAN_SHA = "7385595c3ae43277dda952cf8dc4454f075b3d24b1a5a28613d8402829d2b471"
V3_PLAN = ORIGINAL_PLAN.with_name("plan-bounded-cli-v3.json")
V3_PLAN_SHA = "dcd89935db51b1313202f93e7edacf099d1bd18c7addd005d377b0a52ddc3ec7"
V3_DELIVERY = "57609463e63b76197e1871098f12c2afab76b0dc"
V3_ARCHIVE = ROOT / "reports/container-port-diag-5b-once-stopped"
V3_ARCHIVE_SHA = "ff03d7b670d05f207df8410eb33095652bfaa14ab8aedeb653fb57d3b23e33d0"
PLAN = ORIGINAL_PLAN.with_name("plan-split-cli-home-v4.json")
PLAN_SHA = "ced9a01a7cc79553244fc2c4f69742cee4d54e13f3e391898ab580d6183f9129"
CLI_ENTRY = Path("/usr/bin/docker")
CLI_BINARY = Path("/mnt/wsl/docker-desktop/cli-tools/usr/bin/docker")
CLI_SHA = "6c71cfe71faff949a602946a4df4799b563232e79d0b8b6fd343d0ef081c9621"
SOCKET = Path("/var/run/docker.sock")
ENDPOINT = "unix:///var/run/docker.sock"
ENGINE = "d86d0c2c-42a0-4420-9f0d-0027845314bf"
IMAGE = "sha256:375ed722706d2bc40338803d48e15df32c3eade270ace39e23166d5e045f7b6f"
IMAGE_LABELS = {
    "org.project5.source": "f267a6dbfd3a67aae1e8285e8cca3e05897b1539",
    "org.project5.context": "3691f6470f429d194e5b21eaffc75dc1e9df659141fb1d75dfb2c1c43de7d91a",
}
LABEL = "org.project5.diagnostic=" + RUN_ID
TAG = "container-port-diagnostic-v4"
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
PREFLIGHT = ORDER[:3]
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy")
HTTP_ORDER = ("inside_http", "ubuntu_published_http", "ubuntu_private_socket_http")
V4 = {
    "evaluation/container_port_diagnostic.py", "tests/test_container_port_diagnostic.py",
    "docs/container-port-diagnostic.md", "reports/container-port-diagnostic-preparation/plan-split-cli-home-v4.json",
}
PREFLIGHT_ARGS = (("context", "inspect", "default"), ("info", "--format", "{{json .}}"),
                  ("image", "inspect", IMAGE))


class TargetUncertain(StopRun):
    pass


class Deadline(StopRun):
    pass


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
        raise Deadline("overall_or_operation_timeout")
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


def stamp(info):
    return (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_size,
            info.st_uid, info.st_gid, info.st_mode)


def validate_cli_file(info):
    require(stat.S_ISREG(info.st_mode) and info.st_uid == info.st_gid == 0
            and bool(info.st_mode & stat.S_IXUSR)
            and not info.st_mode & (stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID)
            and 1 <= info.st_size <= 64 * 1024 * 1024, "untrusted_docker_cli_file")


# Linux Landlock UAPI on x86_64. ABI 3 handles truncate; do not silently degrade.
LL_CREATE, LL_ADD_RULE, LL_RESTRICT = 444, 445, 446
LL_HANDLED = sum(1 << bit for bit in (1, *range(4, 15)))
LL_HOME = sum(1 << bit for bit in (1, 4, 5, 7, 8, 14))
HOME_LOG_DIRS = (".docker", ".docker/desktop", ".docker/desktop/log", ".docker/desktop/log/host")
HOME_LOG = HOME_LOG_DIRS[-1] + "/docker-desktop.log"


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def landlock_kernel():
    require(sys.platform == "linux" and os.uname().machine == "x86_64", "linux_x86_64_landlock_required")
    libc = ctypes.CDLL(None, use_errno=True)
    require(libc.syscall(LL_CREATE, ctypes.c_void_p(), ctypes.c_size_t(0), 1) >= 3,
            "landlock_abi3_required")
    return libc


@contextmanager
def cli_write_rules(home, identity):
    require(threading.active_count() == 1, "single_threaded_cli_launch_required")
    libc = landlock_kernel()
    handled = ctypes.c_uint64(LL_HANDLED)
    rules = libc.syscall(LL_CREATE, ctypes.byref(handled), ctypes.c_size_t(ctypes.sizeof(handled)), 0)
    require(rules >= 0, "landlock_ruleset_unavailable")
    try:
        fd = os.open(home, os.O_PATH | os.O_NOFOLLOW | os.O_DIRECTORY)
        try:
            require(directory_identity(os.fstat(fd)) == identity, "docker_cli_home_replaced")
            rule = PathBeneath(LL_HOME, fd)
            require(libc.syscall(LL_ADD_RULE, rules, 1, ctypes.byref(rule), 0) == 0,
                    "landlock_home_rule_rejected")
        finally:
            os.close(fd)

        def restrict_child():
            if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(LL_RESTRICT, rules, 0) != 0:
                raise OSError("landlock_child_restriction_failed")
            os.close(rules)

        yield rules, restrict_child
    finally:
        os.close(rules)


def directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)


def validate_private_dir(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))
            == (10001, 10001, 0o700), "docker_cli_directory_not_private:" + path.name)
    return directory_identity(info)


class Transport:
    def __init__(self, audit):
        self.private = audit
        self.config = audit / "cli-config"
        self.home = audit / "cli-home"
        self._directory_ids = None
        self._cli_stamp = None
        self._initial_identity = None

    def verify_socket(self):
        s = SOCKET.lstat()
        require(stat.S_ISSOCK(s.st_mode) and s.st_uid == 0 and stat.S_IMODE(s.st_mode) == 0o660
                and s.st_gid in os.getgroups(), "unexpected_docker_socket")
        return stamp(s)

    def binding(self):
        # This is a locally observable identity only, not a daemon binding.
        try:
            require(not any(k.upper().startswith("DOCKER_") for k in os.environ),
                    "docker_environment_override_forbidden")
            name = CLI_ENTRY.lstat()
            require(stat.S_ISLNK(name.st_mode) and name.st_uid == 0
                    and os.readlink(CLI_ENTRY) == str(CLI_BINARY)
                    and CLI_BINARY.resolve(strict=True) == CLI_BINARY,
                    "unexpected_docker_cli_path")
            require(0 not in {os.getgid(), *os.getgroups()}, "root_group_not_allowed")
            for parent in set(CLI_ENTRY.parents) | set(CLI_BINARY.parents):
                info = parent.lstat()
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0
                        and (not info.st_mode & stat.S_IWGRP or info.st_gid == 0)
                        and (not info.st_mode & stat.S_IWOTH or
                             (parent == Path("/mnt/wsl") and info.st_mode & stat.S_ISVTX)),
                        "untrusted_docker_cli_parent")
            current = CLI_BINARY.lstat()
            validate_cli_file(current)
            if self._cli_stamp is None:
                with os.fdopen(os.open(CLI_BINARY, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
                    require(stamp(os.fstat(stream.fileno())) == stamp(current)
                            and stream.read(4) == b"\x7fELF", "unexpected_docker_cli_binary")
                    stream.seek(0)
                    require(hashlib.file_digest(stream, "sha256").hexdigest() == CLI_SHA
                            and stamp(os.fstat(stream.fileno())) == stamp(current), "unexpected_docker_cli_digest")
                self._cli_stamp = stamp(current)
            require(stamp(current) == self._cli_stamp, "docker_cli_changed")
            identity = (stamp(name), self._cli_stamp, self.verify_socket())
            if self._initial_identity is None:
                self._initial_identity = identity
            require(identity == self._initial_identity, "docker_local_identity_changed")
            return identity
        except (OSError, StopRun) as error:
            raise TargetUncertain(str(error)) from error

    def guard_paths(self):
        require(self.private.resolve(strict=True) == self.private, "docker_cli_path_traversal")
        ids = tuple(validate_private_dir(p) for p in (self.private, self.config, self.home))
        require(len(set(ids)) == 3, "docker_cli_directory_alias")
        if self._directory_ids is None:
            self._directory_ids = ids
        require(ids == self._directory_ids, "docker_cli_directory_identity_changed")
        require(not any(self.config.iterdir()), "docker_config_not_empty")
        root = self.home
        for name in (*HOME_LOG_DIRS, HOME_LOG):
            target = self.home / name
            unexpected = [child.name for child in root.iterdir() if child != target]
            require(not unexpected, "unexpected_docker_cli_home_entry")
            try:
                info = target.lstat()
            except FileNotFoundError:
                return
            require((info.st_uid, info.st_gid) == (10001, 10001)
                    and stat.S_IMODE(info.st_mode) & 0o7022 == 0, "untrusted_docker_cli_home_entry")
            if name == HOME_LOG:
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 1_000_000,
                        "unexpected_docker_cli_log")
            else:
                require(stat.S_ISDIR(info.st_mode), "unexpected_docker_cli_home_directory")
                root = target

    def command(self, args, seconds, on_launch):
        self.binding()
        self.guard_paths()
        prefix = ["--config", str(self.config)]
        prefix += ["--context", "default"] if tuple(args) == PREFLIGHT_ARGS[0] else ["--host", ENDPOINT]
        deadline, data = time.monotonic() + seconds, bytearray()
        fd = os.open(CLI_BINARY, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            require(stamp(os.fstat(fd)) == self._cli_stamp, "docker_cli_changed_before_exec")
            with cli_write_rules(self.home, self._directory_ids[2]) as (rules, restrict_child):
                process = subprocess.Popen([str(CLI_BINARY), *prefix, *args], executable=f"/proc/self/fd/{fd}",
                                           pass_fds=(fd, rules), preexec_fn=restrict_child, umask=0o077,
                                           cwd=self.home, env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                                                              "HOME": str(self.home), "OTEL_SDK_DISABLED": "true"},
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        finally:
            os.close(fd)
        try:
            on_launch()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise Deadline("docker_command_timeout")
                    for key, _ in selector.select(min(left, .25)):
                        block = os.read(key.fileobj.fileno(), 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                        data.extend(block)
                        require(len(data) <= 1_000_000, "docker_output_limit")
            if process.wait(timeout=max(.01, deadline - time.monotonic())):
                error = TargetUncertain("docker_command_failed")
                error.details = data.decode("utf-8", errors="replace")
                raise error
            self.guard_paths()
            return data.decode("utf-8")
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait(timeout=2)

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
        self.normal_launches = self.cleanup_launches = 0
        self.ids, self.uncertain, self.results = {}, set(), {}
        self.audit = work.with_name(work.name + ".preflight")
        self.journal = None
        self.local_identity = None
        self.docker_uncertainty = None
        self.preflight_receipts = []
        self.removed = set()
        self.preflight_hash = None
        self.claim_reserved = self.claimed = False

    def mark_uncertain(self, action, reason, possibly_committed=False):
        if self.docker_uncertainty is None:
            self.docker_uncertainty = {"action": action, "reason": str(reason),
                                       "possibly_committed": possibly_committed}

    def require_binding(self):
        try:
            current = self.transport.binding()
            if current is None or current != self.local_identity:
                raise TargetUncertain("docker_local_identity_changed")
        except (OSError, StopRun) as error:
            self.mark_uncertain("local_endpoint_check", error)
            raise TargetUncertain(str(error)) from error

    def require_unused(self):
        require(not os.path.lexists(self.work) and not os.path.lexists(self.audit),
                "fixed_workspace_or_preflight_audit_already_exists")

    def reserve_audit(self, commit):
        try:
            self.audit.mkdir(mode=0o700)
        except FileExistsError as error:
            raise StopRun("fixed_preflight_audit_already_exists") from error
        self.sync_directory(self.audit.parent)
        save_new(self.audit / "intent.json", {"run_id": RUN_ID, "source": commit, "plan_sha256": PLAN_SHA})
        for name in ("cli-home", "cli-config"):
            (self.audit / name).mkdir(mode=0o700)
        self.journal = Journal(self.audit / "actions.jsonl")
        self.sync_directory(self.audit)

    def remaining(self, cleanup=False):
        seconds = self.start + (TOTAL - 12 if cleanup else NORMAL_WINDOW) - self.clock()
        if seconds < .1:
            raise Deadline("diagnostic_deadline")
        return seconds

    def receipt(self, name, data):
        self.journal.write({"event": "result", "action": name, "result": data})

    def docker(self, action, *argv, cleanup=False):
        require(self.docker_uncertainty is None, "docker_target_uncertain")
        limit = min(6, self.remaining(cleanup))
        require(self.normal + self.cleanup_count < MAX_DOCKER, "docker_budget")
        require(self.claimed or (not cleanup and action in PREFLIGHT), "docker_action_requires_claim")
        require(not self.claimed or action not in PREFLIGHT, "preflight_already_adopted")
        if cleanup:
            require(self.cleanup_count < 6 and action.startswith("cleanup_"), "cleanup_budget")
        else:
            require(self.normal < len(ORDER) and ORDER[self.normal] == action, "docker_order")
        if action in PREFLIGHT:
            require(argv == PREFLIGHT_ARGS[self.normal], "unexpected_preflight_arguments")
        self.require_binding()
        reserved, launched, result = False, False, None

        def on_launch():
            nonlocal launched
            require(not launched, "duplicate_cli_launch_receipt")
            launched = True
            if cleanup:
                self.cleanup_launches += 1
            else:
                self.normal_launches += 1
            self.journal.write({"event": "launched", "action": action})
        try:
            self.journal.write({"event": "before", "transport": "docker", "action": action, "argv": argv})
            self.require_binding()
            limit = min(limit, self.remaining(cleanup))
            if cleanup:
                self.cleanup_count += 1
            else:
                self.normal += 1
            reserved = True
            result = self.transport.command(list(argv), limit, on_launch)
            require(launched, "missing_cli_launch_receipt")
            self.receipt(action, result)
            self.require_binding()
            if action in PREFLIGHT:
                self.preflight_receipts.append(result)
            return result
        except (Exception, KeyboardInterrupt) as error:
            # Lost status means an operation may have been accepted even if the
            # CLI failed or its receipt could not be durably recorded.
            self.docker_uncertainty = {"action": action, "reason": str(error),
                                       "launch_confirmed": launched,
                                       "possibly_committed": reserved and (
                                           cleanup or action not in (*PREFLIGHT, "inspect_containers", "inspect_networks"))}
            if action in {"network_responder", "network_sentinel", "create_responder", "create_sentinel"}:
                if isinstance(result, str) and re.fullmatch("[0-9a-f]{64}", result.strip()):
                    self.docker_uncertainty["unconfirmed_created_id"] = result.strip()
            self.journal.write({"event": "error", "action": action, **self.docker_uncertainty,
                                "error": f"{type(error).__name__}: {error}",
                                "details": getattr(error, "details", None)})
            raise TargetUncertain(str(error)) from error

    def http(self, action):
        require(self.claimed, "http_action_requires_claim")
        self.require_binding()
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

    def seal_journal(self):
        path = self.journal.path
        with path.open("r+b") as stream:
            path.chmod(0o400)
            os.fsync(stream.fileno())
        return sha(path)

    def seal_audit(self):
        actual = [json.loads(line) for line in self.journal.path.read_bytes().splitlines()]
        expected = []
        for action, args, result in zip(PREFLIGHT, PREFLIGHT_ARGS, self.preflight_receipts, strict=True):
            expected.extend([{"event": "before", "transport": "docker", "action": action, "argv": list(args)},
                             {"event": "launched", "action": action},
                             {"event": "result", "action": action, "result": result}])
        require([{k: v for k, v in row.items() if k != "utc"} for row in actual] == expected,
                "missing_duplicate_or_changed_preflight_receipt")
        self.journal.write({"event": "preflight_verified", "normal_docker": self.normal,
                            "normal_cli_launches": self.normal_launches})
        self.preflight_hash = self.seal_journal()

    def claim(self, commit):
        self.remaining()
        self.require_binding()
        self.transport.guard_paths()
        source = self.audit / "actions.jsonl"
        require(self.normal_launches == self.normal == len(PREFLIGHT) and self.preflight_hash is not None
                and sha(source) == self.preflight_hash, "unsealed_or_changed_preflight_audit")
        prefix = source.read_bytes()
        require(hashlib.sha256(prefix).hexdigest() == self.preflight_hash, "preflight_prefix_changed")
        try:
            self.work.mkdir(mode=0o700)
        except FileExistsError as error:
            raise StopRun("fixed_workspace_or_claim_already_exists") from error
        self.claim_reserved = True
        # mkdir is the exclusive permanent transition. An incomplete workspace
        # after an interruption is still used; no mutating action can follow it.
        self.sync_directory(self.work.parent)
        journal = Journal(self.work / "actions.jsonl")
        with journal.path.open("ab") as stream:
            stream.write(prefix)
            stream.flush()
            os.fsync(stream.fileno())
        require(sha(journal.path) == self.preflight_hash, "execution_preflight_prefix_mismatch")
        save_new(self.work / "claim.json", {"run_id": RUN_ID, "source": commit, "plan_sha256": PLAN_SHA,
                                            "preflight_sha256": self.preflight_hash,
                                            "preflight_bytes": len(prefix)})
        (self.work / "ingress").mkdir(mode=0o700)
        self.sync_directory(self.work)
        self.journal = journal
        self.claimed = True

    @staticmethod
    def sync_directory(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def create(self, role, action, *argv):
        self.uncertain.add(role)
        raw = self.docker(action, *argv)
        try:
            ident = exact_id(raw)
            require(ident not in self.ids.values(), "duplicate_created_resource_id")
        except StopRun as error:
            self.mark_uncertain(action, error, possibly_committed=True)
            raise TargetUncertain(str(error)) from error
        self.ids[role] = ident
        self.uncertain.remove(role)

    def preflight(self):
        contexts = json.loads(self.docker("context", "context", "inspect", "default"))
        require(one(contexts)["Name"] == "default" and one(contexts)["Endpoints"]["docker"]["Host"] == ENDPOINT, "unexpected_docker_context_or_endpoint")
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
        self.construct()
        try:
            target = self.inspect()
        except (KeyError, ValueError, TypeError, StopRun) as error:
            self.mark_uncertain("resource_inspection", error)
            raise TargetUncertain(str(error)) from error
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
        actions = []
        for role in ("sentinel", "responder"):
            if role in self.ids:
                ident = self.ids[role]
                actions.extend([(role, False, ("stop", "--time", "2", ident)),
                                (role, True, ("rm", ident))])
        for role in ("net_sentinel", "net_responder"):
            if role in self.ids:
                actions.append((role, True, ("network", "rm", self.ids[role])))
        for role, removed, args in actions:
            if self.docker_uncertainty is not None:
                break
            name = "cleanup_" + ("remove_" if removed else "stop_") + role
            try:
                self.docker(name, *args, cleanup=True)
            except (OSError, StopRun, KeyboardInterrupt) as error:
                self.mark_uncertain(name, error)
                break
            if removed:
                self.removed.add(role)
        return {**dict.fromkeys(self.uncertain, "unknown_id_manual_review"),
                **{role: ident for role, ident in self.ids.items() if role not in self.removed}}

    def run(self, commit, verify):
        self.require_unused()
        self.local_identity = self.transport.binding()
        self.require_binding()
        self.remaining()
        self.reserve_audit(commit)
        outcome = {"status": "STOPPED", "run_id": RUN_ID, "source": commit, "plan_sha256": PLAN_SHA}
        try:
            require(not os.path.lexists(self.work), "fixed_workspace_or_claim_already_exists")
            self.preflight()
            self.require_binding()
            verify()
            self.seal_audit()
            self.claim(commit)
        except (Exception, KeyboardInterrupt) as error:
            outcome.update(failure=f"{type(error).__name__}: {error}", claim_created=self.claim_reserved,
                           captured_ids={}, normal_docker=self.normal, cleanup_docker=0, http_attempts=0,
                           normal_cli_launches=self.normal_launches, cleanup_cli_launches=0,
                           actual_model_requests=0, docker_uncertainty=self.docker_uncertainty, journal_sha256=(sha(self.journal.path) if self.preflight_hash is not None
                                                                    else self.seal_journal()))
            save_new(self.audit / "result.json", outcome)
            self.sync_directory(self.audit)
            return outcome
        try:
            self.exercise()
            outcome["status"] = "DIAGNOSTIC_COMPLETE"
        except (Exception, KeyboardInterrupt) as error:
            outcome["failure"] = f"{type(error).__name__}: {error}"
            if isinstance(error, (TargetUncertain, Deadline, OSError, KeyboardInterrupt)):
                self.mark_uncertain("diagnostic", error)
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
                           normal_cli_launches=self.normal_launches, cleanup_cli_launches=self.cleanup_launches,
                           actual_model_requests=0, windows_localhost="not_tested", claim_created=True,
                           docker_uncertainty=self.docker_uncertainty,
                           cleanup_owner_review=bool(outcome["unresolved_cleanup"]),
                           preflight_sha256=self.preflight_hash,
                           journal_sha256=self.seal_journal())
            save_new(self.work / "result.json", outcome)
            self.sync_directory(self.work)
        return outcome


def verify_source(commit):
    require(re.fullmatch("[0-9a-f]{40}", commit) and git("rev-parse", "HEAD").decode().strip() == commit,
            "unapproved_source")
    require(not git("status", "--porcelain", "--untracked-files=all"), "dirty_checkout")
    require(sha(ORIGINAL_PLAN) == ORIGINAL_PLAN_SHA, "original_frozen_plan_changed")
    require(sha(PREVIOUS_PLAN) == PREVIOUS_PLAN_SHA, "previous_frozen_plan_changed")
    require(sha(V3_PLAN) == V3_PLAN_SHA, "v3_frozen_plan_changed")
    require(sha(PLAN) == PLAN_SHA, "frozen_plan_changed")
    changed = set(git("diff", "--name-only", "--no-renames", V3_DELIVERY, commit).decode().splitlines())
    require(changed == V4, "unexpected_changes_from_stopped_v3_delivery")
    v3_manifest = V3_ARCHIVE / "checksums.json"
    require(sha(v3_manifest) == V3_ARCHIVE_SHA, "stopped_v3_manifest_changed")
    v3_files = json.loads(v3_manifest.read_bytes())
    require(len(v3_files) == 5 and all(sha(V3_ARCHIVE / name) == digest for name, digest in v3_files.items()),
            "stopped_v3_evidence_changed")
    result = preservation(require_local=True)
    require(len(result["checked"]) == 397, "historical_preservation_count")
    archive_manifest = ARCHIVE / "checksums.json"
    require(sha(archive_manifest) == ARCHIVE_CHECKSUMS, "historical_archive_manifest_changed")
    checksums = json.loads(archive_manifest.read_bytes())
    require(len(checksums) == 31 and all(sha(ARCHIVE / name) == digest for name, digest in checksums.items()),
            "historical_archive_changed")
    return {**result, "archive_checksums": checksums, "stopped_v3_checksums": v3_files}


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
            landlock_kernel()
            require(not os.path.lexists(WORK) and not os.path.lexists(WORK.with_name(WORK.name + ".preflight")),
                    "fixed_workspace_or_preflight_audit_already_exists")
            verify_source(commit)
        audit = WORK.with_name(WORK.name + ".preflight")
        session = Session(Transport(audit), WORK, started=started)
        with alarm(max(.1, TOTAL - (time.monotonic() - started))):
            result = session.run(commit, lambda: verify_source(commit))
        receipt_dir = WORK if session.claimed else session.audit
        print(json.dumps({"status": result["status"], "result": str(receipt_dir / "result.json")}))
        return int(result["status"] != "DIAGNOSTIC_COMPLETE")
    except (StopRun, OSError, subprocess.SubprocessError) as error:
        print(f"BLOCKED_OR_STOPPED: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
