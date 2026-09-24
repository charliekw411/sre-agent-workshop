#!/usr/bin/env python3
"""Read-only checks executed inside the workshop VM."""

import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys


MOUNT = Path("/var/lib/orders")
DATABASE = MOUNT / "orders.db"


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=30).stdout.strip()


def inspect():
    if not MOUNT.is_mount() or not DATABASE.is_file():
        raise RuntimeError("SQLite is missing or /var/lib/orders is not a mounted filesystem.")
    if os.stat(DATABASE).st_dev != os.stat(MOUNT).st_dev or os.stat(MOUNT).st_dev == os.stat("/").st_dev:
        raise RuntimeError("SQLite must reside on the separate mounted data disk, not the OS disk.")
    expected = Path("/dev/disk/azure/scsi1/lun0").resolve(strict=True)
    mounted = json.loads(command("findmnt", "--json", "--output", "SOURCE,FSTYPE,UUID", "--target", str(MOUNT)))
    disk = mounted["filesystems"][0]
    if Path(disk["source"]).resolve(strict=True) != expected or disk["fstype"] != "ext4":
        raise RuntimeError("The Orders filesystem is not the expected ext4 managed disk at LUN 0.")
    service = command("systemctl", "is-active", "orders-api")
    enabled = command("systemctl", "is-enabled", "orders-api")
    user = command("systemctl", "show", "orders-api", "--property=User", "--value")
    restart = command("systemctl", "show", "orders-api", "--property=Restart", "--value")
    if service != "active" or enabled != "enabled" or user != "orders" or restart != "always":
        raise RuntimeError("The non-root Orders service is not enabled, active, and restartable.")
    with sqlite3.connect(f"file:{DATABASE}?mode=ro", uri=True, timeout=5) as database:
        integrity = database.execute("PRAGMA quick_check").fetchone()[0]
        count = database.execute("SELECT COUNT(*) FROM Orders").fetchone()[0]
        seeds = database.execute("SELECT COUNT(*) FROM Orders WHERE OrderId BETWEEN -5 AND -1").fetchone()[0]
        journal = database.execute("PRAGMA journal_mode").fetchone()[0]
    if integrity != "ok" or seeds != 5 or journal != "wal":
        raise RuntimeError("SQLite integrity, idempotent seed rows, or WAL mode is invalid.")
    return {
        "ok": True, "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "sourceSha256": Path("/opt/orders-api/bundle.sha256").read_text().strip(),
        "service": {"active": service, "enabled": enabled, "user": user, "restart": restart},
        "storage": {
            "mount": str(MOUNT), "source": disk["source"], "uuid": disk["uuid"],
            "filesystem": disk["fstype"], "database": str(DATABASE), "separateDisk": True,
            "integrity": integrity, "orders": count, "seedOrders": seeds, "journalMode": journal,
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request_id")
    parser.add_argument("--stages")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{32}", args.request_id):
        raise ValueError("Invalid request ID.")
    result = inspect()
    if args.stages:
        result["stages"] = json.loads(args.stages)
    print(f"WORKSHOP_RESULT:{args.request_id}:" + json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError) as error:
        print(f"VM verification failed: {error}", file=sys.stderr)
        sys.exit(1)
