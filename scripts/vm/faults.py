#!/usr/bin/env python3
"""Bounded, VM-local faults. Invoke only through authenticated Azure Run Command."""

import argparse
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time


MOUNT = Path("/var/lib/orders")
BALLAST = MOUNT / ".workshop-disk-pressure"
CPU_UNIT = "orders-cpu-fault.service"
DISK_UNIT = "orders-disk-fault.service"
RESERVE_BYTES = 128 * 1024 * 1024


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def check_mount():
    if not MOUNT.is_mount() or os.stat(MOUNT).st_dev == os.stat("/").st_dev:
        raise RuntimeError("Refusing fault injection: the managed data disk is not mounted.")
    expected = Path("/dev/disk/azure/scsi1/lun0").resolve(strict=True)
    actual = Path(command("findmnt", "--noheadings", "--output", "SOURCE", "--target", str(MOUNT)))
    if actual.resolve(strict=True) != expected:
        raise RuntimeError("Refusing fault injection on an unexpected disk.")


def usage():
    check_mount()
    data = os.statvfs(MOUNT)
    total = data.f_blocks * data.f_frsize
    available = data.f_bavail * data.f_frsize
    return total, available


def unit_state(unit):
    result = subprocess.run(
        ["systemctl", "show", unit, "--property=LoadState,ActiveState", "--no-pager"],
        capture_output=True, text=True, timeout=20,
    )
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    if result.returncode and values.get("LoadState") != "not-found":
        raise RuntimeError(f"Cannot inspect {unit}: {result.stderr.strip()}")
    if values.get("LoadState") == "not-found":
        return "inactive"
    if not values.get("ActiveState"):
        raise RuntimeError(f"systemd did not return the state of {unit}.")
    return values["ActiveState"]


def ballast_bytes():
    try:
        info = BALLAST.lstat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("Refusing to use an unexpected disk-pressure file.")
    return info.st_size


def status():
    total, available = usage()
    return {
        "ok": True, "cpu": unit_state(CPU_UNIT), "disk": unit_state(DISK_UNIT),
        "diskUsedPercent": round(100 * (total - available) / total, 2),
        "availableBytes": available, "ballastBytes": ballast_bytes(),
    }


def stop(unit):
    if unit_state(unit) not in {"inactive", "failed"}:
        command("systemctl", "stop", unit)


def reset():
    check_mount()
    stop(CPU_UNIT)
    stop(DISK_UNIT)
    ballast_bytes()
    BALLAST.unlink(missing_ok=True)
    return status()


def start_cpu(seconds, workers):
    if unit_state(CPU_UNIT) not in {"inactive", "failed"}:
        raise RuntimeError("CPU pressure is already active; reset it before starting another fault.")
    command(
        "systemd-run", "--quiet", "--collect", "--unit=" + CPU_UNIT, "--service-type=exec",
        "--property=RuntimeMaxSec=" + str(seconds), "--property=KillMode=control-group",
        "--property=Nice=10", "--property=MemoryMax=256M",
        sys.executable, str(Path(__file__).resolve()), "cpu-worker", str(seconds), str(workers),
    )
    result = status()
    if result["cpu"] != "active":
        raise RuntimeError("The CPU-pressure unit did not start.")
    return {**result, "seconds": seconds, "workers": workers}


def allocation_size(total, available, percent):
    amount = int(total * percent / 100) - (total - available)
    if amount < 1024 * 1024:
        raise RuntimeError("The data disk is already at the requested pressure level.")
    if amount > available - RESERVE_BYTES:
        raise RuntimeError("The requested pressure would violate the 128 MiB recovery reserve.")
    return amount


def start_disk(percent, seconds):
    total, available = usage()
    allocation_size(total, available, percent)
    if unit_state(DISK_UNIT) not in {"inactive", "failed"} or ballast_bytes():
        raise RuntimeError("Disk pressure or its ballast already exists; run reset first.")
    command(
        "systemd-run", "--quiet", "--collect", "--unit=" + DISK_UNIT, "--service-type=exec",
        "--property=RuntimeMaxSec=" + str(seconds), "--property=KillMode=control-group",
        "--property=MemoryMax=128M",
        "--property=ExecStopPost=/usr/bin/rm -f -- " + str(BALLAST),
        sys.executable, str(Path(__file__).resolve()), "disk-worker", str(percent), str(seconds),
    )
    for _ in range(20):
        result = status()
        if result["disk"] == "active" and result["ballastBytes"] > 0 and result["diskUsedPercent"] >= percent - 1:
            return {**result, "seconds": seconds, "targetPercent": percent}
        if result["disk"] in {"failed", "inactive"}:
            raise RuntimeError("The disk-pressure worker failed; inspect journalctl -u " + DISK_UNIT)
        time.sleep(1)
    raise RuntimeError("Disk-pressure allocation did not reach its target within twenty seconds.")


def terminate(signum, frame):
    raise SystemExit(0)


def cpu_worker(seconds, workers):
    children = []
    signal.signal(signal.SIGTERM, terminate)
    try:
        for _ in range(workers):
            children.append(subprocess.Popen([
                sys.executable, "-c", "while True: sum(i * i for i in range(10000))",
            ]))
        time.sleep(seconds)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=10)


def disk_worker(percent, seconds):
    total, available = usage()
    amount = allocation_size(total, available, percent)
    signal.signal(signal.SIGTERM, terminate)
    descriptor = os.open(BALLAST, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
    try:
        os.posix_fallocate(descriptor, 0, amount)
        os.fsync(descriptor)
        time.sleep(seconds)
    finally:
        os.close(descriptor)
        BALLAST.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("cpu", "disk", "status", "reset", "cpu-worker", "disk-worker"))
    parser.add_argument("numbers", nargs="*", type=int)
    parser.add_argument("--request-id")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RuntimeError("Use Azure VM Run Command; fault actions require VM administrator privileges.")
    if args.action in {"cpu", "cpu-worker"}:
        if len(args.numbers) != 2 or not 10 <= args.numbers[0] <= 1800 or not 1 <= args.numbers[1] <= 8:
            raise ValueError("CPU fault requires seconds 10..1800 and workers 1..8.")
    elif args.action in {"disk", "disk-worker"}:
        if len(args.numbers) != 2 or not 50 <= args.numbers[0] <= 97 or not 30 <= args.numbers[1] <= 1800:
            raise ValueError("Disk fault requires percent 50..97 and seconds 30..1800.")
    elif args.numbers:
        raise ValueError("status and reset do not accept numeric arguments.")
    if args.action.endswith("-worker"):
        workers = {"cpu-worker": cpu_worker, "disk-worker": disk_worker}
        workers[args.action](*args.numbers)
        return
    if not args.request_id or not re.fullmatch(r"[0-9a-f]{32}", args.request_id):
        raise ValueError("Control-plane actions require a correlated request ID.")
    actions = {"cpu": start_cpu, "disk": start_disk, "status": status, "reset": reset}
    result = actions[args.action](*args.numbers)
    print(f"WORKSHOP_RESULT:{args.request_id}:" + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"VM fault action failed: {error}", file=sys.stderr)
        sys.exit(1)
