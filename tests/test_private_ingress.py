import asyncio
import json
import os
from pathlib import Path
import re
import socket
import sys

import pytest

import ingress_proxy as p

ROOT = Path(__file__).resolve().parents[1]
GET = b"GET /health/live HTTP/1.1\r\nHost: 127.0.0.1:5173\r\n\r\n"
OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nalive"


def test_private_compose_and_image_are_separate_from_frozen_files():
    c = json.loads((ROOT / "compose.ingress.yaml").read_text())
    front, back = (c["services"][x] for x in ("frontend", "backend"))
    assert all("ports" not in svc and svc["pull_policy"] == "never" for svc in c["services"].values())
    assert list(c["networks"]) == ["isolated"]
    assert c["networks"]["isolated"] == {
        "driver": "bridge", "internal": True, "enable_ipv6": False,
        "driver_opts": {"com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                        "com.docker.network.bridge.inhibit_ipv4": "true"},
    }
    assert front["networks"] == ["isolated"] and back["network_mode"] == "service:frontend"
    assert "networks" not in back and back["environment"] == {"RAG_MODEL_MODE": "disabled"}
    assert front["volumes"] == [{
        "type": "bind", "source": "/var/lib/project5-ingress/local",
        "target": "/run/rag-ingress", "read_only": False,
        "bind": {"create_host_path": False, "propagation": "rprivate"}
    }]
    assert [(m["target"], m["read_only"]) for m in back["volumes"]] == [("/var/lib/rag", True)]
    assert all(s["read_only"] and s["cap_drop"] == ["ALL"] and s["security_opt"] == ["no-new-privileges:true"]
               and s["user"] in ("101:101", "10001:10001") and s["mem_limit"] and s["pids_limit"] for s in (front, back))
    cfg = (ROOT / "containers/nginx-ingress.conf").read_text()
    assert re.findall(r"listen ([^;]+);", cfg) == ["unix:/run/rag-ingress/.stage/http.sock"]
    assert set(re.findall(r"proxy_pass ([^;]+);", cfg)) == {"http://127.0.0.1:8000"}
    assert "/health/live" in cfg and "proxy_buffering off;" in cfg and "proxy_next_upstream off;" in cfg
    entry = (ROOT / "containers/ingress-entrypoint.sh").read_text()
    assert entry.index("chmod 0660") < entry.index("ln -T")
    assert 'mkdir "$stage"' in entry and 'umask 077' in entry and "owner=101:11017" in entry and "mountinfo" in entry
    assert "kill -QUIT" in entry and "test -z" in entry


def parse(wire):
    async def read():
        reader = asyncio.StreamReader(limit=p.MAX_HEAD)
        reader.feed_data(wire)
        reader.feed_eof()
        return await p.request(reader)
    return asyncio.run(read())


@pytest.mark.parametrize("wire,code", [
    (GET.replace(b"127.0.0.1:5173", b"evil.test"), 400),
    (GET.replace(b"/health/live", b"http://evil.test/"), 400),
    (GET.replace(b"/health/live", b"//evil.test/"), 400),
    (GET.replace(b"/health/live", b"/api/../foo"), 400),
    (GET.replace(b"/health/live", b"/%2e%2e"), 400),
    (GET.replace(b"HTTP/1.1", b"HTTP/1.0"), 400),
    (GET.replace(b"GET", b"CONNECT"), 405),
    (GET.replace(b"GET", b"POST"), 405),
    (GET[:-2] + b"hOst: localhost:5173\r\n\r\n", 400),
    (GET[:-2] + b"Origin: http://localhost:5173\r\n\r\n", 403),
    (GET[:-2] + b"Origin: null\r\n\r\n", 403),
    (GET[:-2] + b"Forwarded: host=evil\r\n\r\n", 400),
    (GET[:-2] + b"X-Forwarded-Host: evil\r\n\r\n", 400),
    (GET[:-2] + b"Connection: Host, keep-alive\r\n\r\n", 400),
    (GET[:-2] + b"Transfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n", 400),
    (GET[:-2] + b" folded\r\n\r\n", 400),
    (GET[:-2] + b"Host : foo\r\n\r\n", 400),
    (GET[:-2] + b"Content-Length: 000\r\n\r\n", 400),
    (GET[:-2] + b"Content-Length: 4097\r\n\r\n", 413),
])
def test_bad_requests_denied(wire, code):
    with pytest.raises(p.Invalid) as err:
        parse(wire)
    assert err.value.code == code


def test_local_authorities_exact_body_and_no_extra_requests():
    body = b'{"question":"synthetic?"}'
    for host in p.HOSTS:
        wire = (b"POST /api/ask HTTP/1.1\r\nHost: " + host + b"\r\nOrigin: http://" + host +
                b"\r\nContent-Type: application/json\r\nConnection: keep-alive\r\nCookie: hidden=1\r\n"
                b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body + GET)
        method, transformed = parse(wire)
        assert method == b"POST" and transformed.startswith(b"POST /api/ask HTTP/1.0\r\n")
        assert b"cookie" not in transformed and transformed.endswith(body)
        assert b"connection: close\r\n" in transformed and transformed.count(b"HTTP/") == 1


def test_untrusted_instance_names_rejected():
    for value in ("../staging", "/abs", r"external\socket", ".", "x"*41):
        with pytest.raises(OSError):
            p.deployment_path(value)


class Synthetic:
    def __init__(self, handler):
        self.handler = handler
        self.tasks = []

    async def connect(self):
        left, right = socket.socketpair()
        try:
            a, b = await asyncio.open_connection(sock=left, limit=p.MAX_HEAD)
            c, d = await asyncio.open_connection(sock=right, limit=p.MAX_HEAD)
        except BaseException:
            left.close()
            right.close()
            raise
        async def run():
            try:
                await self.handler(c, d)
            finally:
                await p.close(d)
        self.tasks.append(asyncio.create_task(run()))
        return a, b

    async def finish(self):
        await asyncio.gather(*self.tasks)


async def client(proxy):
    left, right = socket.socketpair()
    a, b = await asyncio.open_connection(sock=left)
    c, d = await asyncio.open_connection(sock=right, limit=p.MAX_HEAD)
    proxy.accept(c, d)
    return a, b


def test_fixed_ipv4_listener_contract(monkeypatch):
    async def verify():
        expected = object()
        async def fake(callback, host, port, **kwargs):
            assert host == "127.0.0.1" and port == 5173 and kwargs["family"] == socket.AF_INET
            assert kwargs["backlog"] == p.CONCURRENCY and kwargs["limit"] == p.MAX_HEAD
            return expected
        monkeypatch.setattr(p.asyncio, "start_server", fake)
        px = p.Proxy(None)
        await px.start()
        assert px.server is expected
    asyncio.run(verify())


@pytest.mark.parametrize("upstream,expected", [
    (OK, b"200"),
    (b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 14\r\n\r\nmodel_disabled', b"503"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 1\r\nTransfer-Encoding: chunked\r\n\r\n", b"502"),
    (b"HTTP/1.0 200 OK\r\nContent-Length: 9999999\r\n\r\n", b"502"),
])
def test_response_status_and_bounded_framing(upstream, expected):
    async def check():
        async def handler(r, w):
            assert (await r.readuntil(b"\r\n\r\n")).startswith(b"GET /health/live HTTP/1.0\r\n")
            w.write(upstream)
            await w.drain()
        origin = Synthetic(handler)
        proxy = p.Proxy(origin)
        r, w = await client(proxy)
        w.write(GET)
        await w.drain()
        raw = await asyncio.wait_for(r.read(), 2)
        assert raw.split(b" ")[1] == expected
        if upstream == OK:
            assert raw.endswith(b"alive")
        if expected == b"503":
            assert raw.endswith(b"model_disabled")
        await p.close(w)
        await proxy.stop()
        await origin.finish()
    asyncio.run(check())


@pytest.mark.parametrize("with_length", [True, False])
def test_first_response_bytes_arrive_before_upstream_finishes(with_length):
    async def check():
        second = asyncio.Event()
        async def handler(r, w):
            await r.readuntil(b"\r\n\r\n")
            w.write(b"HTTP/1.0 200 OK\r\n" + (b"Content-Length: 6\r\n" if with_length else b"") + b"\r\none")
            await w.drain()
            await second.wait()
            w.write(b"two")
            await w.drain()
        origin = Synthetic(handler)
        proxy = p.Proxy(origin)
        r, w = await client(proxy)
        w.write(GET)
        await w.drain()
        hdr = await asyncio.wait_for(r.readuntil(b"\r\n\r\n"), 2)
        if not with_length:
            assert b"transfer-encoding: chunked" in hdr
        assert await asyncio.wait_for(r.readexactly(3 if with_length else 8), 2) == (b"one" if with_length else b"3\r\none\r\n")
        second.set()
        assert b"two" in await asyncio.wait_for(r.read(), 2)
        await p.close(w)
        await proxy.stop()
        await origin.finish()
    asyncio.run(check())


def test_header_and_body_deadlines_and_busy_shutdown(monkeypatch):
    async def check():
        monkeypatch.setattr(p, "CONCURRENCY", 1)
        monkeypatch.setattr(p, "BODY_TIME", 0.03)
        px = p.Proxy(None)
        r, w = await client(px)
        w.write(b"POST /api/ask HTTP/1.1\r\nHost: localhost:5173\r\nContent-Type: application/json\r\nContent-Length: 4\r\n\r\nx")
        await w.drain()
        r2, w2 = await client(px)
        assert await asyncio.wait_for(r2.read(), 1) == b""
        assert b" 408 " in await asyncio.wait_for(r.read(), 1)
        await p.close(w2)
        await p.close(w)
        r3, w3 = await client(px)
        await px.stop()
        assert await asyncio.wait_for(r3.read(), 1) == b""
        await p.close(w3)
    asyncio.run(check())


def test_header_limit_and_unavailable_or_denied_socket():
    async def check():
        class Unavailable:
            async def connect(self):
                raise PermissionError("synthetic denial")
        for req, expected in [(GET, b" 503 "), (b"GET / HTTP/1.1\r\nX-Large: " + b"a"*9000, b" 431 ")]:
            px = p.Proxy(Unavailable())
            r, w = await client(px)
            w.write(req)
            await w.drain()
            assert expected in await asyncio.wait_for(r.read(), 1)
            await px.stop()
            await p.close(w)
    asyncio.run(check())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux Unix peer credentials and inode checks")
def test_private_socket_mode_stale_replaced_and_peer_credentials(tmp_path):
    async def check():
        tmp_path.chmod(0o2710)
        uid, gid = os.getuid(), os.getgid()
        policy = (uid, gid, (uid, gid))
        path = tmp_path / "http.sock"
        with pytest.raises(FileNotFoundError):
            p.PrivateSocket(tmp_path, policy=policy, ancestors=False)
        sock = socket.socket(socket.AF_UNIX)
        sock.bind(str(path))
        path.chmod(0o666)
        with pytest.raises(OSError, match="unsafe ingress socket"):
            p.PrivateSocket(tmp_path, policy=policy, ancestors=False)
        path.chmod(0o660)
        stale = p.PrivateSocket(tmp_path, policy=policy, ancestors=False)
        sock.close()
        with pytest.raises(ConnectionRefusedError):
            await stale.connect()
        path.unlink()
        tmp_path.joinpath("http.sock").symlink_to("elsewhere")
        with pytest.raises(OSError):
            stale.verify()
        stale.close()
        path.unlink()
        closed_clients = asyncio.Queue()
        async def fake(reader, writer):
            await p.close(writer)
            closed_clients.put_nowait(True)
        # Emulate the protected Linux hard-link publication from the supervisor.
        stage = tmp_path / ".stage"
        stage.mkdir(mode=0o700)
        staged = stage / "http.sock"
        server = await asyncio.start_unix_server(fake, str(staged))
        staged.chmod(0o660)
        os.link(staged, path)
        staged.unlink()
        good = p.PrivateSocket(tmp_path, policy=policy, ancestors=False)
        wrong_peer = p.PrivateSocket(tmp_path, policy=(uid, gid, (uid + 1, gid)), ancestors=False)
        _, writer = await good.connect()
        await p.close(writer)
        await asyncio.wait_for(closed_clients.get(), 1)
        with pytest.raises(OSError, match="peer credentials"):
            await wrong_peer.connect()
        await asyncio.wait_for(closed_clients.get(), 1)
        tmp_path.chmod(0o2770)
        with pytest.raises(OSError, match="unsafe ingress directory"):
            good.verify()
        tmp_path.chmod(0o2710)
        os.rename(path, tmp_path / "retained-inode")
        replacement = socket.socket(socket.AF_UNIX)
        replacement.bind(str(path))
        path.chmod(0o660)
        with pytest.raises(OSError, match="endpoint changed"):
            good.verify()
        replacement.close()
        server.close()
        await server.wait_closed()
        good.close()
        wrong_peer.close()
    asyncio.run(check())


def test_runtime_acceptance_plan_budgets_and_fixture_are_frozen():
    import hashlib
    base = ROOT / "reports/private-ingress-preparation"
    frozen = json.loads((base / "plan.json").read_text())
    fixture = (base / "fixture.json").read_bytes()
    assert frozen["run_id"] == "container-runtime-5b-private-uds-v1-once"
    assert frozen["status"] == "proposed_not_executed"
    assert hashlib.sha256(fixture).hexdigest() == frozen["fixture_sha256"]
    assert all(hashlib.sha256((ROOT / f).read_bytes()).hexdigest() == digest
               for f, digest in frozen["input_sha256"].items() if f != "containers/ingress-entrypoint.sh")
    limits = frozen["limits"]
    assert len(frozen["metadata"]) == limits["metadata"] == 5
    assert len(frozen["normal_actions"]) == limits["normal"] == 17
    assert len(frozen["cleanup"]) == limits["cleanup"] == 5
    assert limits["docker_total"] == limits["metadata"] + limits["normal"] + limits["cleanup"]
    assert len(frozen["requests"]) == limits["http_total"] == 10
    assert limits["windows_http"] == limits["model_requests"] == limits["retries"] == 0
