"""Bounded host ingress for the dedicated Nginx HTTP Unix socket."""
import argparse
import asyncio
from contextlib import suppress
import os
from pathlib import Path
import re
import signal
import socket
import stat
import struct
import sys

BIND = ("127.0.0.1", 5173)
BASE = Path("/var/lib/project5-ingress")
PROXY_UID, GROUP, NGINX_UID = 11018, 11017, 101
MAX_HEAD, MAX_BODY, MAX_REPLY, CONCURRENCY = 8192, 4096, 4 * 1024 * 1024, 16
BODY_TIME, CONNECT_TIME, TOTAL_TIME, IO_TIME = 7, 2, 200, 10
HOSTS = (b"127.0.0.1:5173", b"localhost:5173")
TOKEN = re.compile(rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
TARGET = re.compile(rb"/[A-Za-z0-9_./~?&=+-]{0,1023}")
PASS_REQUEST = {b"host", b"origin", b"content-type", b"accept", b"if-none-match", b"if-modified-since"}
PASS_RESPONSE = {b"content-type", b"content-length", b"cache-control", b"last-modified", b"etag",
                 b"x-content-type-options", b"referrer-policy", b"content-security-policy", b"x-request-id"}
HOP_DENIED = {b"transfer-encoding", b"upgrade", b"forwarded", b"te", b"trailer", b"expect", b"keep-alive"}
STATUS = {400: b"Bad Request", 403: b"Forbidden", 405: b"Method Not Allowed",
          408: b"Request Timeout", 413: b"Content Too Large", 431: b"Request Header Fields Too Large",
          502: b"Bad Gateway", 503: b"Service Unavailable", 504: b"Gateway Timeout"}


class Invalid(ValueError):
    def __init__(self, code):
        self.code = code


def identity(info):
    return info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode


def ext4_ingress(directory):
    """Require native ext4 and reject a mount on/inside the deployment subtree."""
    with Path("/proc/self/mountinfo").open() as source:
        data = source.read(1_000_001)
    if len(data) > 1_000_000:
        raise OSError("mount table limit")
    best = None
    for row in data.splitlines():
        left, sep, right = row.partition(" - ")
        if not sep:
            raise OSError("malformed mount table")
        mount = Path(left.split()[4])
        if mount == directory or directory in mount.parents:
            raise OSError("unexpected ingress mount boundary")
        if mount in directory.parents:
            candidate = len(mount.parts), right.split()[0]
            if best is None or candidate[0] >= best[0]:
                best = candidate
    if not best or best[1] != "ext4":
        raise OSError("native ext4 required")


class PrivateSocket:
    """Pin deployment parent and socket inode until the proxy is stopped."""
    def __init__(self, directory, *, policy=(NGINX_UID, GROUP, (NGINX_UID, NGINX_UID)), ancestors=True):
        self.directory, self.policy, self.ancestors = directory, policy, ancestors
        self.fd = self.an = None
        try:
            self.fd = self.open_dir()
            self.an = identity(os.fstat(self.fd))
            self.stamp = self.socket_stamp()
        except BaseException:
            self.close()
            raise

    def open_dir(self):
        uid, gid, _ = self.policy
        if not self.directory.is_absolute() or ".." in self.directory.parts:
            raise OSError("unsafe ingress path")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fd = os.open("/", flags)
        try:
            for index, name in enumerate(self.directory.parts[1:], 2):
                final = index == len(self.directory.parts)
                # The proxy has search permission but may not list the service directory.
                flags = (os.O_PATH if final else os.O_RDONLY) | os.O_DIRECTORY | os.O_NOFOLLOW
                child = os.open(name, flags, dir_fd=fd)
                os.close(fd)
                fd = child
                info = os.fstat(fd)
                if final and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, gid, 0o2710):
                    raise OSError("unsafe ingress directory")
                if not final and self.ancestors and (info.st_uid or info.st_gid or info.st_mode & 0o022):
                    raise OSError("untrusted ingress ancestor")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def socket_stamp(self):
        uid, gid, _ = self.policy
        info = os.stat("http.sock", dir_fd=self.fd, follow_symlinks=False)
        if (not stat.S_ISSOCK(info.st_mode) or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink)
                != (uid, gid, 0o660, 1) or info.st_dev != os.fstat(self.fd).st_dev):
            raise OSError("unsafe ingress socket")
        return identity(info) + (info.st_ctime_ns,)

    def verify(self):
        if self.ancestors:
            ext4_ingress(self.directory)
        fd = self.open_dir()
        try:
            if identity(os.fstat(fd)) != self.an or self.socket_stamp() != self.stamp:
                raise OSError("ingress endpoint changed; restart the proxy only after verification")
        finally:
            os.close(fd)

    async def connect(self):
        self.verify()
        reader, writer = await asyncio.open_unix_connection(f"/proc/self/fd/{self.fd}/http.sock", limit=MAX_HEAD)
        try:
            self.verify()
            peer = writer.get_extra_info("socket")
            _, uid, gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if (uid, gid) != self.policy[2]:
                raise OSError("unexpected ingress peer credentials")
            return reader, writer
        except BaseException:
            writer.transport.abort()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


async def head(reader):
    raw = await reader.readuntil(b"\r\n\r\n")
    if len(raw) > MAX_HEAD:
        raise Invalid(431)
    first, *lines = raw[:-4].split(b"\r\n")
    if len(lines) > 40:
        raise Invalid(431)
    fields = {}
    for line in lines:
        key, sep, raw_value = line.partition(b":")
        value = raw_value.strip(b" \t")
        key = key.lower()
        if (not sep or not TOKEN.fullmatch(key) or key in fields or
                any(not 32 <= n < 127 for n in value)):
            raise Invalid(400)
        fields[key] = value
    return first, fields


def content_length(headers, cap, too_large):
    length = headers.get(b"content-length")
    if length is None:
        return None
    if not re.fullmatch(rb"0|[1-9][0-9]{0,8}", length):
        raise Invalid(400)
    size = int(length)
    if size > cap:
        raise Invalid(too_large)
    return size


def encode_head(first, fields):
    return first + b"\r\n" + b"".join(k + b": " + v + b"\r\n" for k, v in fields.items()) + b"\r\n"


async def request(reader):
    first, fields = await head(reader)
    parts = first.split(b" ")
    if len(parts) != 3 or parts[2] != b"HTTP/1.1":
        raise Invalid(400)
    method, target, _ = parts
    path = target.partition(b"?")[0]
    if not TARGET.fullmatch(target) or b"//" in path or any(p in (b".", b"..") for p in path.split(b"/")):
        raise Invalid(400)
    if method not in (b"GET", b"HEAD", b"POST") or method == b"POST" and path != b"/api/ask":
        raise Invalid(405)
    host, origin = fields.get(b"host"), fields.get(b"origin")
    if host not in HOSTS:
        raise Invalid(400)
    if origin is not None and origin != b"http://" + host:
        raise Invalid(403)
    if (any(k in HOP_DENIED or k.startswith((b"x-forwarded-", b"proxy-", b"x-original-")) for k in fields)
            or fields.get(b"connection", b"close").lower() not in (b"keep-alive", b"close")):
        raise Invalid(400)
    length = content_length(fields, MAX_BODY, 413)
    if method == b"POST":
        if length is None or fields.get(b"content-type", b"").lower() != b"application/json":
            raise Invalid(400)
    elif length:
        raise Invalid(400)
    payload = await reader.readexactly(length or 0)
    selected = {k: v for k, v in fields.items() if k in PASS_REQUEST}
    if length is not None:
        selected[b"content-length"] = str(length).encode()
    # HTTP/1.0 has a close-delimited fallback and does not request chunked upstream.
    selected[b"connection"] = b"close"
    return method, encode_head(method + b" " + target + b" HTTP/1.0", selected) + payload


async def close(writer):
    if writer is not None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=0.2)
        except (TimeoutError, OSError):
            writer.transport.abort()


async def send_error(writer, code):
    first = b"HTTP/1.1 " + str(code).encode() + b" " + STATUS[code]
    with suppress(OSError, TimeoutError):
        writer.write(encode_head(first, {b"content-length": b"0", b"connection": b"close",
                                         b"cache-control": b"no-store"}))
        await asyncio.wait_for(writer.drain(), timeout=0.2)


async def response_head(reader, method):
    first, fields = await head(reader)
    match = re.fullmatch(rb"HTTP/1\.[01] ([2-5][0-9]{2})(?: [ -~]*)?", first)
    if not match or any(k in fields for k in (b"transfer-encoding", b"upgrade", b"trailer")):
        raise Invalid(502)
    code = int(match[1])
    length = content_length(fields, MAX_REPLY, 502)
    zero = method == b"HEAD" or code in (204, 205, 304)
    if code in (204, 205) and length not in (0, None):
        raise Invalid(502)
    forwarded = {k: v for k, v in fields.items() if k in PASS_RESPONSE}
    if code in (204, 304):
        forwarded.pop(b"content-length", None)
    forwarded[b"connection"] = b"close"
    chunked = not zero and length is None
    if chunked:
        forwarded[b"transfer-encoding"] = b"chunked"
    return encode_head(b"HTTP/1.1 " + first.split(b" ", 1)[1], forwarded), 0 if zero else length, chunked


async def stream(reader, writer, length, chunked):
    remaining = MAX_REPLY if length is None else length
    while remaining:
        amount = min(65536, remaining)
        async with asyncio.timeout(IO_TIME):
            part = await reader.read(amount)
            if not part:
                if not chunked:
                    raise Invalid(502)
                writer.write(b"0\r\n\r\n")
                await writer.drain()
                return
            if chunked:
                part_wire = f"{len(part):x}\r\n".encode() + part + b"\r\n"
            else:
                part_wire = part
            writer.write(part_wire)
            await writer.drain()
        remaining -= len(part)
    if chunked:
        async with asyncio.timeout(IO_TIME):
            if await reader.read(1):
                raise Invalid(502)
            writer.write(b"0\r\n\r\n")
            await writer.drain()


class Proxy:
    def __init__(self, upstream):
        self.upstream = upstream
        self.tasks = set()
        self.server = None
        self.stopping = False

    def accept(self, reader, writer):
        if self.stopping or len(self.tasks) >= CONCURRENCY:
            writer.transport.abort()
            return
        task = asyncio.create_task(self.handle(reader, writer))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def handle(self, reader, writer):
        try:
            await self.exchange(reader, writer)
        finally:
            writer.transport.abort()

    async def exchange(self, reader, writer):
        upstream = None
        started = False
        try:
            async with asyncio.timeout(BODY_TIME):
                method, message = await request(reader)
        except asyncio.LimitOverrunError:
            await send_error(writer, 431)
        except (Invalid, asyncio.IncompleteReadError, TimeoutError, OSError) as error:
            await send_error(writer, error.code if isinstance(error, Invalid) else 408 if isinstance(error, TimeoutError) else 400)
        else:
            try:
                async with asyncio.timeout(TOTAL_TIME):
                    async with asyncio.timeout(CONNECT_TIME):
                        upstream_reader, upstream = await self.upstream.connect()
                    upstream.write(message)
                    await upstream.drain()
                    response, length, chunked = await response_head(upstream_reader, method)
                    writer.write(response)
                    started = True
                    await writer.drain()
                    await stream(upstream_reader, writer, length, chunked)
            except (Invalid, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError, OSError) as error:
                if not started:
                    await send_error(writer, 504 if isinstance(error, TimeoutError) else
                                     503 if upstream is None else 502)
                else:
                    writer.transport.abort()
            finally:
                await close(upstream)
        await close(writer)

    async def start(self):
        self.server = await asyncio.start_server(self.accept, *BIND, family=socket.AF_INET,
                                                backlog=CONCURRENCY, limit=MAX_HEAD)

    async def stop(self):
        self.stopping = True
        if self.server is not None:
            self.server.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.server is not None:
            await self.server.wait_closed()


def deployment_path(instance):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", instance):
        raise OSError("invalid ingress instance")
    return BASE / instance


async def run(instance):
    path = deployment_path(instance)
    if (not sys.platform.startswith("linux") or os.getuid() != PROXY_UID or os.geteuid() != PROXY_UID
            or os.getgid() != GROUP or os.getegid() != GROUP or set(os.getgroups()) - {GROUP}):
        raise OSError("dedicated unprivileged ingress account required")
    ext4_ingress(path)
    upstream = PrivateSocket(path)
    proxy = Proxy(upstream)
    done = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, done.set)
    try:
        await proxy.start()
        await done.wait()
    finally:
        try:
            await proxy.stop()
        finally:
            upstream.close()


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--instance", default="local")
    try:
        asyncio.run(run(args.parse_args().instance))
    except OSError as error:
        raise SystemExit(f"ingress unavailable: {error}") from error


if __name__ == "__main__":
    main()
