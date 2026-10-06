#!/usr/bin/env python3
"""Read-only checks executed inside the workshop VM."""

import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request


ENVIRONMENT_FILE = Path("/etc/orders-api.env")


def command(*args, environment=None):
    try:
        return subprocess.run(
            args,
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        ).stdout.strip()
    except subprocess.CalledProcessError as error:
        diagnostic = (error.stderr or error.stdout or "no diagnostic output").strip()
        if environment:
            for name in ("PGPASSWORD",):
                secret = environment.get(name)
                if secret:
                    diagnostic = diagnostic.replace(secret, "<redacted>")
        raise RuntimeError(
            f"{args[0]} exited with code {error.returncode}: {diagnostic[:2000]}"
        ) from None


def settings():
    values = {}
    for line in ENVIRONMENT_FILE.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name] = value.strip('"')
    connection = values.get("ConnectionStrings__OrdersDb", "")
    parts = {}
    for item in connection.split(";"):
        if "=" in item:
            name, value = item.split("=", 1)
            parts[name.strip().casefold()] = value.strip()
    required = {"host", "port", "database", "username", "ssl mode"}
    if set(parts).intersection(required) != required:
        raise RuntimeError("The Orders PostgreSQL configuration is incomplete.")
    if parts["ssl mode"].casefold() != "verifyfull":
        raise RuntimeError("PostgreSQL certificate verification is not enabled.")
    if values.get("OrdersDatabase__Authentication") != "ManagedIdentity":
        raise RuntimeError("The Orders API is not configured for managed identity.")
    if "password" in parts:
        raise RuntimeError("The Azure PostgreSQL connection must not contain a password.")
    return parts


def access_token():
    query = urllib.parse.urlencode({
        "api-version": "2018-02-01",
        "resource": "https://ossrdbms-aad.database.windows.net",
    })
    request = urllib.request.Request(
        f"http://169.254.169.254/metadata/identity/oauth2/token?{query}",
        headers={"Metadata": "true"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=10) as response:
        payload = json.loads(response.read(1024 * 1024))
    token = payload.get("access_token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Managed identity did not return a PostgreSQL access token.")
    return token


def postgresql_environment(database):
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "PGHOST": database["host"],
        "PGPORT": database["port"],
        "PGDATABASE": database["database"],
        "PGUSER": database["username"],
        "PGPASSWORD": access_token(),
        "PGSSLMODE": "verify-full",
        "PGSSLROOTCERT": "system",
        "PGCONNECT_TIMEOUT": "5",
    }


def inspect():
    service = command("systemctl", "is-active", "orders-api")
    enabled = command("systemctl", "is-enabled", "orders-api")
    user = command("systemctl", "show", "orders-api", "--property=User", "--value")
    restart = command("systemctl", "show", "orders-api", "--property=Restart", "--value")
    if service != "active" or enabled != "enabled" or user != "orders" or restart != "always":
        raise RuntimeError(
            "The non-root Orders service is not enabled, active, and restartable.")

    database = settings()
    addresses = {
        item[4][0] for item in socket.getaddrinfo(
            database["host"], int(database["port"]), type=socket.SOCK_STREAM)
    }
    if not addresses or any(
            not ipaddress.ip_address(address).is_private for address in addresses):
        raise RuntimeError("PostgreSQL did not resolve exclusively to private addresses.")

    environment = postgresql_environment(database)
    sql = """
SELECT json_build_object(
    'serverVersion', current_setting('server_version'),
    'migrationVersion', COALESCE((SELECT MAX(version) FROM orders_schema_migrations), 0),
    'orders', (SELECT COUNT(*) FROM orders),
    'seedOrders', (SELECT COUNT(*) FROM orders WHERE order_id BETWEEN -5 AND -1)
)::text;
"""
    status = json.loads(command(
        "psql", "--no-psqlrc", "--tuples-only", "--no-align",
        "--set", "ON_ERROR_STOP=1", "--command", sql, environment=environment,
    ))
    environment["PGPASSWORD"] = ""
    if status["migrationVersion"] < 1 or status["seedOrders"] != 5:
        raise RuntimeError(
            "PostgreSQL migration state or deterministic seed rows are invalid.")
    if not str(status["serverVersion"]).startswith("16."):
        raise RuntimeError("The Orders database is not PostgreSQL 16.")

    live = json.loads(command(
        "curl", "--fail", "--silent", "--show-error", "--max-time", "5",
        "http://127.0.0.1:8080/health/live",
    ))
    ready = json.loads(command(
        "curl", "--fail", "--silent", "--show-error", "--max-time", "5",
        "http://127.0.0.1:8080/health/ready",
    ))
    if live.get("status") != "live" or ready.get("status") != "ready":
        raise RuntimeError("The local Orders API health endpoints are not ready.")

    return {
        "ok": True,
        "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "sourceSha256": Path("/opt/orders-api/bundle.sha256").read_text().strip(),
        "service": {
            "active": service,
            "enabled": enabled,
            "user": user,
            "restart": restart,
        },
        "database": {
            "provider": "PostgreSQL",
            "privateDns": True,
            "tlsMode": "verify-full",
            **status,
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
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
        urllib.error.URLError,
    ) as error:
        print(f"VM verification failed: {error}", file=sys.stderr)
        sys.exit(1)
