#!/usr/bin/env python3
"""Bounded workshop fault controls. Invoke only through Azure Run Command."""

import argparse
import json
from pathlib import Path
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request


CPU_UNIT = "orders-cpu-fault.service"
API_UNIT = "orders-api.service"
API_ROOT = "http://127.0.0.1:8080"
DATABASE_VERIFICATION_SECONDS = 150
DATABASE_UNAVAILABLE_TITLE = "Orders database unavailable"
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def command(*args):
    return subprocess.run(
        args, check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()


def unit_state(unit):
    result = subprocess.run(
        ["systemctl", "show", unit, "--property=LoadState,ActiveState", "--no-pager"],
        capture_output=True,
        text=True,
        timeout=20,
    )
    values = dict(
        line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode and values.get("LoadState") != "not-found":
        raise RuntimeError(f"Cannot inspect {unit}: {result.stderr.strip()}")
    if values.get("LoadState") == "not-found":
        return "inactive"
    if not values.get("ActiveState"):
        raise RuntimeError(f"systemd did not return the state of {unit}.")
    return values["ActiveState"]


def cpu_status():
    return {"ok": True, "cpu": unit_state(CPU_UNIT)}


def reset_cpu():
    if unit_state(CPU_UNIT) not in {"inactive", "failed"}:
        command("systemctl", "stop", CPU_UNIT)
    return cpu_status()


def start_cpu(seconds, workers):
    if unit_state(CPU_UNIT) not in {"inactive", "failed"}:
        raise RuntimeError(
            "CPU pressure is already active; reset it before starting another fault.")
    command(
        "systemd-run",
        "--quiet",
        "--collect",
        "--unit=" + CPU_UNIT,
        "--service-type=exec",
        "--property=RuntimeMaxSec=" + str(seconds),
        "--property=KillMode=control-group",
        "--property=Nice=10",
        "--property=MemoryMax=256M",
        sys.executable,
        str(Path(__file__).resolve()),
        "cpu-worker",
        str(seconds),
        str(workers),
    )
    result = cpu_status()
    if result["cpu"] != "active":
        raise RuntimeError("The CPU-pressure unit did not start.")
    return {**result, "seconds": seconds, "workers": workers}


def api_pid():
    value = command(
        "systemctl", "show", API_UNIT, "--property=MainPID", "--value",
    )
    try:
        pid = int(value)
    except ValueError as error:
        raise RuntimeError(
            f"systemd returned an invalid {API_UNIT} process ID.") from error
    if pid <= 0:
        raise RuntimeError(f"{API_UNIT} does not have a running process.")
    return pid


def request_json(path):
    request = urllib.request.Request(
        API_ROOT + path,
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        response = HTTP.open(request, timeout=8)
    except urllib.error.HTTPError as error:
        response = error
    except (TimeoutError, urllib.error.URLError) as error:
        raise RuntimeError(
            f"Orders API {path} could not be reached: {error}") from error
    with response:
        body = response.read(65537)
        status_code = response.status
    if len(body) > 65536:
        raise RuntimeError(f"Orders API {path} returned an oversized response.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Orders API {path} returned invalid JSON with HTTP {status_code}.") from error
    return status_code, payload


def database_status():
    live_code, live = request_json("/health/live")
    if (
        live_code != 200
        or not isinstance(live, dict)
        or live.get("status") != "live"
    ):
        raise RuntimeError(
            "Orders API liveness did not return the expected live response.")

    ready_code, ready = request_json("/health/ready")
    orders_code, orders = request_json("/orders")
    if (
        ready_code == 200
        and isinstance(ready, dict)
        and ready.get("status") == "ready"
        and orders_code == 200
        and isinstance(orders, list)
    ):
        connectivity = "ready"
    elif (
        ready_code == 503
        and isinstance(ready, dict)
        and ready.get("title") == DATABASE_UNAVAILABLE_TITLE
        and orders_code == 503
        and isinstance(orders, dict)
        and orders.get("title") == DATABASE_UNAVAILABLE_TITLE
    ):
        connectivity = "unavailable"
    else:
        raise RuntimeError(
            "Orders API PostgreSQL probes were inconsistent: "
            f"readiness HTTP {ready_code}, orders HTTP {orders_code}.")
    return {
        **cpu_status(),
        "apiPid": api_pid(),
        "apiRecycled": False,
        "postgresqlConnectivity": connectivity,
        "liveStatusCode": live_code,
        "readinessStatusCode": ready_code,
        "ordersStatusCode": orders_code,
    }


def wait_for_api_live(deadline):
    last_error = "The liveness endpoint did not respond."
    while time.monotonic() < deadline:
        try:
            status_code, payload = request_json("/health/live")
            if (
                status_code == 200
                and isinstance(payload, dict)
                and payload.get("status") == "live"
            ):
                return
            last_error = f"Liveness returned HTTP {status_code}."
        except RuntimeError as error:
            last_error = str(error)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(1, remaining))
    raise RuntimeError(
        "The recycled Orders API did not become live. "
        f"Last observation: {last_error}")


def recycle_api_and_verify(expected):
    if expected not in {"ready", "unavailable"}:
        raise ValueError("Expected PostgreSQL state must be ready or unavailable.")
    previous_pid = api_pid()
    deadline = time.monotonic() + DATABASE_VERIFICATION_SECONDS
    attempts = 0
    last_error = "No verification attempt completed."
    while time.monotonic() < deadline:
        attempts += 1
        command("systemctl", "restart", API_UNIT)
        try:
            wait_for_api_live(min(deadline, time.monotonic() + 30))
            result = database_status()
            if result["postgresqlConnectivity"] == expected:
                if result["apiPid"] == previous_pid:
                    raise RuntimeError(
                        f"{API_UNIT} retained its process ID after restart.")
                return {
                    **result,
                    "apiRecycled": True,
                    "previousApiPid": previous_pid,
                    "apiRecycleAttempts": attempts,
                }
            last_error = (
                "PostgreSQL connectivity was "
                f"{result['postgresqlConnectivity']}, expected {expected}.")
        except RuntimeError as error:
            last_error = str(error)
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(5, remaining))
    raise RuntimeError(
        "The Orders API-only recycle did not verify PostgreSQL connectivity as "
        f"{expected} within {DATABASE_VERIFICATION_SECONDS} seconds. "
        f"Last observation: {last_error}")


def verify_postgresql_denied():
    return recycle_api_and_verify("unavailable")


def verify_postgresql_ready():
    return recycle_api_and_verify("ready")


def terminate(signum, frame):
    raise SystemExit(0)


def cpu_worker(seconds, workers):
    children = []
    signal.signal(signal.SIGTERM, terminate)
    try:
        for _ in range(workers):
            children.append(subprocess.Popen([
                sys.executable,
                "-c",
                "while True: sum(i * i for i in range(10000))",
            ]))
        time.sleep(seconds)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "cpu",
            "status",
            "reset",
            "postgresql-denied",
            "postgresql-ready",
            "postgresql-status",
            "cpu-worker",
        ),
    )
    parser.add_argument("numbers", nargs="*", type=int)
    parser.add_argument("--request-id")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError(
            "Use Azure VM Run Command; fault actions require VM administrator privileges.")
    if args.action in {"cpu", "cpu-worker"}:
        if (len(args.numbers) != 2
                or not 10 <= args.numbers[0] <= 1800
                or not 1 <= args.numbers[1] <= 8):
            raise ValueError(
                "CPU fault requires seconds 10..1800 and workers 1..8.")
    elif args.numbers:
        raise ValueError("This action does not accept numeric arguments.")
    if args.action == "cpu-worker":
        cpu_worker(*args.numbers)
        return
    if not args.request_id or not re.fullmatch(r"[0-9a-f]{32}", args.request_id):
        raise ValueError(
            "Control-plane actions require a correlated request ID.")
    actions = {
        "cpu": start_cpu,
        "status": cpu_status,
        "reset": reset_cpu,
        "postgresql-denied": verify_postgresql_denied,
        "postgresql-ready": verify_postgresql_ready,
        "postgresql-status": database_status,
    }
    result = actions[args.action](*args.numbers)
    print(
        f"WORKSHOP_RESULT:{args.request_id}:"
        + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ) as error:
        print(f"VM fault action failed: {error}", file=sys.stderr)
        sys.exit(1)
