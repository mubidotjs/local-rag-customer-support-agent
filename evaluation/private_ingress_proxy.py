"""Owner-launched proxy-account companion for the separately authorized acceptance."""
import argparse
import asyncio
import json
import os
import re
import signal
import sys
import time

import ingress_proxy
from evaluation import private_ingress_runtime as steps
from evaluation.lifecycle_live import StopRun


async def message(name, seconds):
    end = time.monotonic() + seconds
    directory = steps.CONTROL / "operator"
    while time.monotonic() < end:
        try:
            return steps.handoff_read(directory, name + ".json", 10001, 11017)
        except FileNotFoundError:
            await asyncio.sleep(.08)
    raise StopRun("proxy_message_timeout:" + name)


async def observe_stop(params, end, stop_requested):
    while time.monotonic() < end:
        if stop_requested.is_set():
            raise StopRun("proxy_owner_interrupted")
        command = steps.CONTROL / "operator"
        for stage in ("socket_removed", "cancel_observer"):
            try:
                control = steps.handoff_read(command, stage + ".json", 10001, 11017)
            except FileNotFoundError:
                continue
            steps.check(control == params, "proxy_control_nonce")
            if stage == "socket_removed":
                steps.check(all(not os.path.lexists(steps.INGRESS / n) for n in ("http.sock", ".stage")),
                            "stale_frontend_socket_or_stage")
            acknowledge(stage, params)
            return
        await asyncio.sleep(.08)
    raise StopRun("proxy_observer_timeout")


def acknowledge(stage, params):
    directory = steps.CONTROL / "proxy"
    steps.private_directory(directory, 11018, 10001, 0o2750)
    steps.handoff_write(directory, stage + ".json", {**params, "stage": stage, "uid": 11018, "gid": 11017})


async def serve(commit):
    steps.check(sys.platform.startswith("linux") and os.getuid() == os.geteuid() == 11018
                and os.getgid() == os.getegid() == 11017 and set(os.getgroups()) <= {11017},
                "dedicated_proxy_identity")
    steps.source(commit)
    for dirname, (uid, gid, mode) in {"": (0, 0, 0o755), "operator": (10001, 11017, 0o2750),
                                     "proxy": (11018, 10001, 0o2750)}.items():
        steps.private_directory(steps.CONTROL / dirname, uid, gid, mode)
    ingress_proxy.ext4_ingress(steps.CONTROL)
    ingress_proxy.ext4_ingress(steps.INGRESS)
    steps.check(not any((steps.CONTROL / "proxy").iterdir()), "proxy_handoff_already_used")
    params = await message("start", 5)
    end = params["deadline"]
    steps.check(params.get("run") == steps.RUN and params.get("source") == commit
                and re.fullmatch(r"[0-9a-f]{48}", params.get("nonce", ""))
                and type(params.get("inode")) is int and type(end) in (int, float)
                and 0 < end - time.monotonic() <= 600, "unapproved_proxy_handoff")
    upstream = ingress_proxy.PrivateSocket(steps.INGRESS)
    proxy = ingress_proxy.Proxy(upstream)
    stopped = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stopped.set)
    proxy_closed = False
    try:
        steps.check(upstream.stamp[1] == params["inode"], "handoff_socket_inode_mismatch")
        # Exclusively used even if bind fails; the operator will classify the missing receipt as uncertain.
        steps.handoff_write(steps.CONTROL / "proxy", "intent.json", params)
        async with asyncio.timeout(max(0, end - time.monotonic())):
            await proxy.start()
            steps.check([s.getsockname() for s in proxy.server.sockets] == [ingress_proxy.BIND], "unexpected_listener")
            acknowledge("start", params)
            while not stopped.is_set():
                try:
                    control = steps.handoff_read(steps.CONTROL / "operator", "stop.json", 10001, 11017)
                except FileNotFoundError:
                    await asyncio.sleep(.08)
                    continue
                steps.check(control == params, "proxy_stop_nonce")
                await proxy.stop()
                proxy_closed = True
                acknowledge("stop", params)
                await observe_stop(params, end, stopped)
                return
            raise StopRun("host_proxy_interrupted")
    finally:
        try:
            if not proxy_closed:
                await proxy.stop()
        finally:
            upstream.close()


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--coordinate-authorized-once", required=True)
    args.add_argument("--plan-sha256", required=True)
    options = args.parse_args()
    try:
        steps.check(options.plan_sha256 == steps.PLAN_SHA and not os.environ.get("PYTEST_CURRENT_TEST"),
                    "explicit_authorized_frozen_plan_required")
        asyncio.run(serve(options.coordinate_authorized_once))
    except (StopRun, OSError, ValueError, TimeoutError) as error:
        print(json.dumps({"status": "STOPPED", "error": str(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
