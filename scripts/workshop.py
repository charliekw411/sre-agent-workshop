#!/usr/bin/env python3
"""Cross-platform deployment, smoke checks, and authenticated VM fault actions."""

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[1]
PROVIDERS = {
    "Microsoft.App", "Microsoft.Compute", "Microsoft.Network",
    "Microsoft.OperationalInsights", "Microsoft.Insights",
    "Microsoft.ManagedIdentity", "Microsoft.AlertsManagement",
}
SAFE_OUTPUTS = {
    "AZURE_ENV_NAME", "AZURE_LOCATION", "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP", "RESOURCE_GROUP", "SUBSCRIPTION_ID", "TENANT_ID",
    "WORKSHOP_SUFFIX", "LOCATION", "VM_NAME", "VM_RESOURCE_ID",
    "DATA_DISK_NAME", "DATA_DISK_RESOURCE_ID", "PUBLIC_IP_ADDRESS",
    "PUBLIC_IP_RESOURCE_ID", "ORDERS_API_FQDN", "SERVICE_ORDERS_API_ENDPOINT_URL",
    "LOG_ANALYTICS_NAME", "LOG_ANALYTICS_ID", "LOG_ANALYTICS_CUSTOMER_ID",
    "APP_INSIGHTS_NAME", "APP_INSIGHTS_RESOURCE_ID", "VIRTUAL_NETWORK_NAME",
    "DATA_COLLECTION_RULE_ID", "SRE_AGENT_NAME", "SRE_AGENT_RESOURCE_ID",
    "SRE_AGENT_ENDPOINT", "SRE_AGENT_PRINCIPAL_ID", "SRE_AGENT_IDENTITY_RESOURCE_ID",
    "SRE_AGENT_IDENTITY_PRINCIPAL_ID",
}
SOURCE_FILES = ("*.cs", "*.csproj", "appsettings.json", "wwwroot/**/*")
VM_FILES = ("install.sh", "orders-api.service", "faults.py", "inspect.py")
TRANSIENT_HTTP = {408, 429, 502, 503, 504}
SRE_RESPONSE_PLAN_NAME = "workshop-sev1-sev2-review"
FRESH_ENVIRONMENT_EVIDENCE = (
    "provision-start.json",
    "vm-deployment.json",
    "sre-agent-configuration.json",
    "smoke.json",
    "vm-inspection.json",
    "persistence-witness.json",
    "restart.json",
    "telemetry.json",
    "fault-cpu.json",
    "fault-disk.json",
    "fault-status.json",
    "fault-reset.json",
    "fresh-benchmark.json",
)


class DeploymentError(Exception):
    pass


class HttpError(DeploymentError):
    def __init__(self, status, path):
        self.status = status
        super().__init__(f"Public Orders API {path} returned HTTP {status}.")


class SreAgentRequestError(DeploymentError):
    def __init__(self, message, *, status=None, retryable=False):
        self.status = status
        self.retryable = retryable
        super().__init__(message)


def progress(message):
    print(message, file=sys.stderr, flush=True)


def cli(tool, *args, timeout=180, sensitive=False):
    executable = shutil.which(tool)
    if not executable:
        raise DeploymentError(f"Install {tool} before running azd up.")
    process_env = os.environ.copy()
    process_env.update({
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "AZURE_CORE_ENABLE_LOG_FILE": "false",
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
        "AZD_SKIP_UPDATE_CHECK": "true",
    })
    command = [executable, *args]
    if tool == "az":
        command += ["--only-show-errors", "--output", "json"]
    try:
        result = subprocess.run(
            command, cwd=ROOT, env=process_env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise DeploymentError(
            f"{tool} {' '.join(args[:2])} exceeded {timeout}s. An Azure operation may "
            "still be running; inspect its status before submitting it again."
        ) from error
    if result.returncode:
        details = "Credential-related output withheld." if sensitive else result.stderr.strip()[-3000:]
        raise DeploymentError(
            f"{tool} {' '.join(args[:2])} failed (exit {result.returncode}). {details}"
        )
    return result.stdout


def az(*args, **kwargs):
    output = cli("az", *args, **kwargs)
    try:
        return json.loads(output) if output.strip() else None
    except json.JSONDecodeError as error:
        raise DeploymentError(f"az {' '.join(args[:2])} returned invalid JSON.") from error


def required(values, key):
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"Missing {key}; select the intended azd environment and run azd up.")
    return value


def environment():
    values = json.loads(cli("azd", "env", "get-values", "--output", "json"))
    if not isinstance(values, dict):
        raise DeploymentError("azd did not return an environment object.")
    env_name = required(values, "AZURE_ENV_NAME")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", env_name):
        raise DeploymentError("The azd environment name must contain only letters, digits, - and _.")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    try:
        uuid.UUID(subscription)
    except ValueError as error:
        raise DeploymentError("AZURE_SUBSCRIPTION_ID must be a subscription GUID.") from error
    # Every call uses the azd subscription, not an unrelated Azure CLI default.
    az("account", "set", "--subscription", subscription)
    return values


def state_directory(values):
    directory = ROOT / ".workshop" / required(values, "AZURE_ENV_NAME")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def clear_fresh_environment_evidence(values):
    directory = state_directory(values)
    removed = []
    for filename in FRESH_ENVIRONMENT_EVIDENCE:
        path = directory / filename
        if path.exists():
            path.unlink()
            removed.append(filename)
    if removed:
        progress(
            f"[preflight] Cleared {len(removed)} stale validation artifact(s) "
            "for the new resource group."
        )


def record_stage(values, name, elapsed, status="succeeded", **details):
    path = state_directory(values) / "deployment.json"
    report = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"stages": []}
    report["stages"].append({
        "name": name, "seconds": round(elapsed, 3), "status": status, **details,
    })
    write_json(path, report)


@contextmanager
def stage(values, name):
    started = time.monotonic()
    progress(f"[{name}] Starting")
    try:
        yield
    except (DeploymentError, OSError, ValueError, KeyError, TypeError) as error:
        elapsed = time.monotonic() - started
        record_stage(values, name, elapsed, "failed")
        raise DeploymentError(f"[{name}] {error}") from error
    elapsed = time.monotonic() - started
    record_stage(values, name, elapsed)
    progress(f"[{name}] Completed in {elapsed:.1f}s")


def export_values(values):
    directory = ROOT / ".workshop"
    directory.mkdir(exist_ok=True)
    safe = {key: str(value) for key, value in values.items()
            if key in SAFE_OUTPUTS and value is not None}
    for filename, content in (
        ("workshop.env", "".join(f"export {key}={shlex.quote(value)}\n"
                                for key, value in sorted(safe.items()))),
        ("workshop.ps1", "".join(f"$env:{key} = '{value.replace(chr(39), chr(39) * 2)}'\n"
                                for key, value in sorted(safe.items()))),
    ):
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)


def register_providers():
    def states():
        return {item["namespace"].casefold(): item["state"].casefold() for item in az(
            "provider", "list", "--query", "[].{namespace:namespace,state:registrationState}"
        )}

    current = states()
    pending = {provider for provider in PROVIDERS
               if current.get(provider.casefold()) != "registered"}
    for provider in sorted(pending):
        progress(f"[preflight] Registering {provider}")
        az("provider", "register", "--namespace", provider)
    deadline = time.monotonic() + 600
    while pending:
        if time.monotonic() >= deadline:
            raise DeploymentError(
                "Provider registration exceeded ten minutes: " + ", ".join(sorted(pending))
            )
        time.sleep(10)
        current = states()
        pending = {provider for provider in pending
                   if current.get(provider.casefold()) != "registered"}
        if pending:
            progress("[preflight] Waiting for " + ", ".join(sorted(pending)))


def token_identity(token):
    segment = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    return str(uuid.UUID(claims["oid"])), str(uuid.UUID(claims["tid"]))


def check_resource_group(values, fresh=False):
    group = "rg-sre-agent-workshop-" + required(values, "AZURE_ENV_NAME")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    exists = az("group", "exists", "--name", group, "--subscription", subscription)
    if type(exists) is not bool:
        raise DeploymentError("Unexpected resource group existence response.")
    if not exists:
        return False
    if fresh:
        raise DeploymentError(
            f"Benchmark requires a new empty environment; {group} already exists. "
            "Create and select a new azd environment. No resources were changed."
        )
    tags = az("group", "show", "--name", group, "--subscription", subscription, "--query", "tags") or {}
    if tags.get("workshop-architecture") != "single-vm":
        raise DeploymentError(
            f"{group} is not a single-VM workshop environment. Use a new azd environment; "
            "legacy resources and data will not be migrated or deleted."
        )
    return True


def ensure_ssh_public_key(values):
    if values.get("VM_SSH_PUBLIC_KEY"):
        if not re.fullmatch(r"ssh-rsa [A-Za-z0-9+/=]+(?: [^\r\n]+)?",
                            values["VM_SSH_PUBLIC_KEY"]):
            raise DeploymentError("VM_SSH_PUBLIC_KEY must be a single-line RSA OpenSSH public key.")
        return
    with tempfile.TemporaryDirectory(prefix="sre-vm-key-") as temporary:
        path = Path(temporary) / "admin"
        # Azure Linux VM provisioning requires a key, but no SSH ingress is open.
        # Administration uses Run Command; the unused private key is never retained.
        cli("ssh-keygen", "-q", "-t", "rsa", "-b", "3072", "-N", "",
            "-C", "workshop-control-plane-only", "-f", str(path))
        public_key = path.with_suffix(".pub").read_text(encoding="utf-8").strip()
    cli("azd", "env", "set", "VM_SSH_PUBLIC_KEY", public_key,
        "--environment", required(values, "AZURE_ENV_NAME"))
    values["VM_SSH_PUBLIC_KEY"] = public_key


def existing_vm_matches(values, resource_group_exists=None):
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    size = values.get("VM_SIZE") or "Standard_D2as_v5"
    if not re.fullmatch(r"Standard_[A-Za-z0-9_]+", size):
        raise DeploymentError("VM_SIZE must be a Standard Azure VM size name.")
    existing_id = values.get("VM_RESOURCE_ID")
    if not existing_id:
        return False
    group = required(values, "RESOURCE_GROUP")
    if resource_group_exists is None:
        resource_group_exists = az(
            "group", "exists", "--name", group, "--subscription", subscription,
        )
        if type(resource_group_exists) is not bool:
            raise DeploymentError("Unexpected resource group existence response.")
    if not resource_group_exists:
        return False
    existing = az("vm", "list", "--resource-group", group, "--subscription", subscription)
    for vm in existing:
        if vm.get("id", "").casefold() == existing_id.casefold():
            if vm.get("hardwareProfile", {}).get("vmSize", "").casefold() == size.casefold():
                progress(f"[preflight] {size}: existing VM matches the selected environment.")
                return True
            raise DeploymentError(
                "Changing VM_SIZE on an existing environment requires a separate, explicitly planned resize. "
                "Use the deployed size for idempotent azd up."
            )
    return False


def check_vm_capacity(values, check_existing=True):
    location = required(values, "AZURE_LOCATION")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    size = values.get("VM_SIZE") or "Standard_D2as_v5"
    if not re.fullmatch(r"Standard_[A-Za-z0-9_]+", size):
        raise DeploymentError("VM_SIZE must be a Standard Azure VM size name.")
    if check_existing and existing_vm_matches(values):
        return
    skus = az("vm", "list-skus", "--location", location, "--resource-type", "virtualMachines",
              "--size", size, "--all", "--subscription", subscription)
    matches = [sku for sku in skus if sku.get("name", "").casefold() == size.casefold()]
    if len(matches) != 1:
        raise DeploymentError(f"VM size {size} is not advertised in {location} for this subscription.")
    sku = matches[0]
    for restriction in sku.get("restrictions", []):
        if restriction.get("type", "").casefold() == "location":
            raise DeploymentError(
                f"{size} is restricted in {location}: {restriction.get('reasonCode', 'SKU restriction')}. "
                "Choose an available VM_SIZE in this region. No subscription upgrade or quota increase is attempted."
            )
    capabilities = {item["name"]: item["value"] for item in sku.get("capabilities", [])}
    if capabilities.get("CpuArchitectureType", "").casefold() != "x64":
        raise DeploymentError(f"{size} is not x64; the Ubuntu image and published application require x64.")
    if "V2" not in capabilities.get("HyperVGenerations", "").split(","):
        raise DeploymentError(f"{size} does not support the selected Generation 2 Ubuntu image.")
    cpus = int(capabilities["vCPUs"])
    memory = float(capabilities["MemoryGB"])
    if memory < 4:
        progress(
            f"[preflight] WARNING: {size} has {memory:g} GiB RAM. On-VM compilation plus AMA "
            "has not been validated at this size; prefer at least 4 GiB funded by trial credit."
        )
    usage = az("vm", "list-usage", "--location", location, "--subscription", subscription)
    quotas = {row["name"]["value"].casefold(): row for row in usage}
    for name in ("cores", sku["family"]):
        quota = quotas.get(name.casefold())
        if quota is None:
            raise DeploymentError(
                f"Azure did not return the {name} quota in {location}; check Compute registration and subscription eligibility."
            )
        counts = {}
        for field in ("limit", "currentValue"):
            value = quota[field]
            if type(value) is int and value >= 0:
                counts[field] = value
            elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value):
                counts[field] = int(value)
            else:
                raise DeploymentError(f"Azure returned an invalid {field} for the {name} quota.")
        available = counts["limit"] - counts["currentValue"]
        if available < cpus:
            raise DeploymentError(
                f"{size} needs {cpus} vCPUs, but {name} has {available} remaining in {location}. "
                "Select an available size within existing quotas. Free Trial subscriptions cannot request quota increases."
            )
    progress(f"[preflight] {size}: {cpus} x64 vCPUs, {memory:g} GiB RAM; regional and family quotas available.")


def validate():
    config = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    if config.get("services"):
        raise DeploymentError("This VM deployment must not declare Container Apps services.")
    steps = config.get("workflows", {}).get("up", {}).get("steps")
    if steps != [{"azd": "provision"}]:
        raise DeploymentError("azd up must provision and run the postprovision VM deployment hook.")
    for hook in ("preup", "preprovision", "postprovision"):
        if set(config.get("hooks", {}).get(hook, {})) != {"windows", "posix"}:
            raise DeploymentError(f"{hook} requires both Windows and POSIX entrypoints.")
    source = list((ROOT / "src" / "OrdersApi").glob("*.cs"))
    if not source or not (ROOT / "src" / "OrdersApi" / "OrdersApi.csproj").is_file():
        raise DeploymentError("Orders API sources are missing.")
    for filename in ("index.html", "app.css", "app.js"):
        if not (ROOT / "src" / "OrdersApi" / "wwwroot" / filename).is_file():
            raise DeploymentError(f"Orders browser asset is missing: {filename}")
    for filename in VM_FILES:
        if not (ROOT / "scripts" / "vm" / filename).is_file():
            raise DeploymentError(f"VM configuration file is missing: {filename}")
    progress("VM deployment manifest and source files are valid.")


def prepare():
    values = environment()
    write_json(state_directory(values) / "deployment.json", {
        "environment": values["AZURE_ENV_NAME"],
        "startedUtc": datetime.now(timezone.utc).isoformat(), "stages": [],
    })
    with stage(values, "preflight"):
        validate()
        resource_group_exists = check_resource_group(values)
        if not resource_group_exists:
            clear_fresh_environment_evidence(values)
        token = az("account", "get-access-token", "--resource",
                   "https://management.azure.com/", sensitive=True)["accessToken"]
        azd_token = json.loads(cli(
            "azd", "auth", "token", "--scope", "https://management.azure.com/.default",
            "--output", "json", sensitive=True,
        ))["token"]
        principal, _ = token_identity(token)
        if token_identity(token) != token_identity(azd_token):
            raise DeploymentError("Azure CLI and azd must use the same principal and tenant.")
        account = az("account", "show")
        principal_type = "User" if account["user"]["type"] == "user" else "ServicePrincipal"
        for key, value in (
            ("AZURE_PRINCIPAL_ID", principal),
            ("AZURE_PRINCIPAL_TYPE", principal_type),
        ):
            if values.get(key) != value:
                cli("azd", "env", "set", key, value,
                    "--environment", values["AZURE_ENV_NAME"])
                values[key] = value
        if existing_vm_matches(values, resource_group_exists):
            progress(
                "[preflight] Existing VM deployment detected; skipping provider, region, "
                "SKU, and unused-quota availability checks."
            )
        else:
            register_providers()
            location = required(values, "AZURE_LOCATION")
            locations = az("provider", "show", "--namespace", "Microsoft.App", "--query",
                           "resourceTypes[?resourceType=='agents'].locations | [0]")
            if not isinstance(locations, list) or location.casefold() not in {
                    item.replace(" ", "").casefold() for item in locations}:
                raise DeploymentError(
                    f"SRE Agent is not advertised in {location}; no region substitution is made."
                )
            check_vm_capacity(values, check_existing=False)
        ensure_ssh_public_key(values)
        export_values(values)


def preprovision():
    values = environment()
    if not values.get("VM_SSH_PUBLIC_KEY"):
        raise DeploymentError("Run azd up first so preup can prepare the VM parameters.")
    check_resource_group(values)
    write_json(state_directory(values) / "provision-start.json", {"started": time.time()})
    progress("[infrastructure] Provisioning VM, monitoring, and SRE Agent")


def bundle():
    files = {}
    project = ROOT / "src" / "OrdersApi"
    for pattern in SOURCE_FILES:
        for path in sorted(project.glob(pattern)):
            if path.is_file() and not path.is_symlink():
                relative = path.relative_to(project).as_posix()
                files["app/" + relative] = path.read_bytes().replace(b"\r\n", b"\n")
    for filename in VM_FILES:
        files["vm/" + filename] = (ROOT / "scripts" / "vm" / filename).read_bytes().replace(b"\r\n", b"\n")
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, content in sorted(files.items()):
            info = tarfile.TarInfo(name)
            info.size = len(content)
            info.mode = 0o644
            info.mtime = 0
            archive.addfile(info, io.BytesIO(content))
    packed = gzip.compress(stream.getvalue(), mtime=0)
    return base64.b64encode(packed).decode("ascii"), hashlib.sha256(packed).hexdigest()


def run_result(response, request_id):
    entries = response.get("value") if isinstance(response, dict) else None
    if not isinstance(entries, list) or not entries:
        raise DeploymentError("VM Run Command returned no execution status.")
    messages = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise DeploymentError("VM Run Command returned an invalid execution status.")
        message = entry.get("message", "")
        if isinstance(message, str):
            messages.append(message)
        if str(entry.get("code", "")).lower().endswith("/failed") or entry.get("level") == "Error":
            raise DeploymentError(f"VM Run Command failed: {message[-3000:]}")
    output = "\n".join(messages)
    prefix = f"WORKSHOP_RESULT:{request_id}:"
    results = [line[len(prefix):] for line in output.splitlines() if line.startswith(prefix)]
    if len(results) != 1:
        raise DeploymentError(
            "VM script did not return its correlated success record. "
            "Run Command transport success is not script success.\n" + output[-3000:]
        )
    try:
        result = json.loads(results[0])
    except json.JSONDecodeError as error:
        raise DeploymentError("VM script returned invalid result JSON.") from error
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise DeploymentError(f"VM script verification failed: {results[0][:2000]}")
    return result


def run_vm(values, body, timeout=900):
    request_id = uuid.uuid4().hex
    body = body.replace("__WORKSHOP_REQUEST_ID__", request_id)
    delimiter = "WORKSHOP_" + request_id
    script = f"exec /bin/bash <<'{delimiter}'\nset -euo pipefail\n{body}\n{delimiter}\n"
    if len(script.encode("utf-8")) > 64 * 1024:
        raise DeploymentError("VM Run Command payload exceeds the workshop's 64 KiB limit.")
    with tempfile.TemporaryDirectory(prefix="sre-vm-command-") as temporary:
        path = Path(temporary) / "run.sh"
        path.write_text(script, encoding="utf-8", newline="\n")
        response = az(
            "vm", "run-command", "invoke",
            "--resource-group", required(values, "RESOURCE_GROUP"),
            "--name", required(values, "VM_NAME"), "--command-id", "RunShellScript",
            "--subscription", required(values, "AZURE_SUBSCRIPTION_ID"),
            "--scripts", "@" + str(path), timeout=timeout,
        )
    return run_result(response, request_id)


def deploy_vm(values):
    app_id = required(values, "APP_INSIGHTS_RESOURCE_ID")
    connection_string = az("resource", "show", "--ids", app_id,
                           "--api-version", "2020-02-02",
                           "--query", "properties.ConnectionString", sensitive=True)
    if not isinstance(connection_string, str) or not connection_string.startswith("InstrumentationKey="):
        raise DeploymentError("Application Insights did not return an ingestion connection string.")
    packed, digest = bundle()
    settings = base64.b64encode(json.dumps({
        "applicationInsightsConnectionString": connection_string,
    }).encode("utf-8")).decode("ascii")
    body = f"""
work=$(mktemp -d /var/tmp/orders-deploy.XXXXXXXX)
trap 'rm -rf -- "$work"' EXIT
printf '%s' '{packed}' | base64 --decode > "$work/source.tar.gz"
printf '%s  %s\\n' '{digest}' "$work/source.tar.gz" | sha256sum --check --status
tar --extract --gzip --file "$work/source.tar.gz" --directory "$work" --no-same-owner
/bin/bash "$work/vm/install.sh" "$work" '{digest}' '{settings}' '__WORKSHOP_REQUEST_ID__'
"""
    result = run_vm(values, body)
    if result.get("sourceSha256") != digest:
        raise DeploymentError("The VM did not verify the exact application bundle just submitted.")
    write_json(state_directory(values) / "vm-deployment.json", result)
    return result


class SreNoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeploymentError("Unexpected redirect from the Azure SRE Agent.")


def sre_agent_url(values):
    url = required(values, "SRE_AGENT_ENDPOINT").rstrip("/")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.port is not None
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment
            or not parsed.hostname or not parsed.hostname.endswith(".azuresre.ai")):
        raise DeploymentError("Unexpected Azure SRE Agent endpoint.")
    return url


def sre_agent_token():
    response = az(
        "account", "get-access-token", "--resource", "https://azuresre.dev",
        sensitive=True,
    )
    token = response.get("accessToken") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise DeploymentError("Azure CLI did not return an Azure SRE Agent access token.")
    return token


def sre_agent_request(endpoint, token, path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        endpoint + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.build_opener(SreNoRedirect).open(request, timeout=20) as response:
            payload = response.read(128 * 1024 + 1)
            if len(payload) > 128 * 1024:
                raise DeploymentError("Azure SRE Agent response exceeded the expected size.")
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        detail = error.read(4096).decode("utf-8", errors="replace").strip()
        if detail:
            try:
                document = json.loads(detail)
                if isinstance(document, dict) and isinstance(document.get("errors"), dict):
                    detail = "; ".join(
                        f"{field}: {', '.join(messages) if isinstance(messages, list) else messages}"
                        for field, messages in document["errors"].items()
                    )
                elif isinstance(document, dict):
                    detail = (
                        document.get("message")
                        or document.get("detail")
                        or document.get("title")
                        or detail
                    )
            except json.JSONDecodeError:
                pass
            detail = " ".join(str(detail).split())[:1000]
        retryable = error.code in {403, 404, 408, 409, 429, 500, 502, 503, 504}
        raise SreAgentRequestError(
            f"Azure SRE Agent returned HTTP {error.code}"
            + (f": {detail}" if detail else "."),
            status=error.code,
            retryable=retryable,
        ) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
        raise SreAgentRequestError(
            f"Azure SRE Agent request failed: {error}",
            retryable=True,
        ) from error
    except json.JSONDecodeError as error:
        raise DeploymentError("Azure SRE Agent returned invalid JSON.") from error


def ensure_sre_response_plan(values, attempts=8, retry_seconds=15):
    endpoint = sre_agent_url(values)
    token = sre_agent_token()
    properties = {
        "incidentPlatform": "AzMonitor",
        "impactedService": "",
        "priorities": ["Sev1", "Sev2"],
        "incidentType": "",
        "alertId": "",
        "titleContains": "",
        "titleContainsAll": [],
        "titleContainsAny": [],
        "titleNotContains": [],
        "handlingAgent": "meta_agent",
        "handlingAgents": None,
        "owningTeamId": "",
        "owningTeamIds": [],
        "agentMode": "Review",
        "maxAutomatedInvestigationAttempts": 3,
        "mergeEnabled": True,
        "mergeWindowHours": 3,
        "isEnabled": True,
        "icmFilterSettings": None,
        "azMonitorFilterSettings": {
            "targetResourceType": "",
            "targetResource": "",
        },
    }
    document = {
        "name": SRE_RESPONSE_PLAN_NAME,
        "type": "IncidentFilter",
        "tags": [],
        "properties": properties,
    }
    path = "/api/v2/extendedAgent/incidentFilters/" + SRE_RESPONSE_PLAN_NAME
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            sre_agent_request(endpoint, token, path, "PUT", document)
            response = sre_agent_request(
                endpoint, token, "/api/v2/extendedAgent/incidentFilters",
            )
            plans = response.get("value") if isinstance(response, dict) else None
            if not isinstance(plans, list):
                raise DeploymentError("Azure SRE Agent returned an invalid response-plan collection.")
            plan = next(
                (item for item in plans
                 if isinstance(item, dict) and item.get("name") == SRE_RESPONSE_PLAN_NAME),
                None,
            )
            actual = plan.get("properties") if isinstance(plan, dict) else None
            if (not isinstance(actual, dict)
                    or actual.get("incidentPlatform") != "AzMonitor"
                    or actual.get("isEnabled") is not True
                    or set(actual.get("priorities", [])) != {"Sev1", "Sev2"}
                    or actual.get("handlingAgent") != "meta_agent"
                    or actual.get("agentMode") != "Review"):
                raise SreAgentRequestError(
                    "Azure SRE Agent response plan is not visible yet.",
                    retryable=True,
                )
            result = {
                "ok": True,
                "name": SRE_RESPONSE_PLAN_NAME,
                "incidentPlatform": "AzMonitor",
                "priorities": ["Sev1", "Sev2"],
                "mode": "Review",
                "handlingAgent": "meta_agent",
            }
            write_json(state_directory(values) / "sre-agent-configuration.json", result)
            progress("[sre-agent] Enabled the Sev1/Sev2 Review response plan.")
            return result
        except SreAgentRequestError as error:
            last_error = error
            if not error.retryable or attempt == attempts:
                raise
            progress(
                f"[sre-agent] Response plan is not ready; retrying in "
                f"{retry_seconds}s ({attempt}/{attempts})."
            )
            time.sleep(retry_seconds)
    raise DeploymentError(f"Azure SRE Agent response plan failed: {last_error}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeploymentError("Unexpected redirect from the public Orders API.")


def api_url(values):
    url = required(values, "SERVICE_ORDERS_API_ENDPOINT_URL")
    parsed = urllib.parse.urlsplit(url)
    fqdn = required(values, "ORDERS_API_FQDN")
    if (parsed.scheme != "http" or parsed.hostname != fqdn or parsed.port != 8080
            or parsed.username or parsed.password or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment or not fqdn.endswith(".cloudapp.azure.com")):
        raise DeploymentError("Unexpected Orders API endpoint; expected its Azure public DNS name on port 8080.")
    return url.rstrip("/")


def request_json(url, path, method="GET", body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url + path, data=data, method=method, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
            data = response.read(64 * 1024 + 1)
            if len(data) > 64 * 1024:
                raise DeploymentError(f"Orders API {path} exceeded the expected response size.")
            return response.status, json.loads(data) if data else None
    except urllib.error.HTTPError as error:
        raise HttpError(error.code, path) from None
    except json.JSONDecodeError as error:
        raise DeploymentError(f"Orders API {path} did not return JSON.") from error


def await_ready(values, seconds=120):
    url = api_url(values)
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            status, response = request_json(url, "/health/ready")
            if status != 200 or not isinstance(response, dict) or response.get("status") != "ready":
                raise DeploymentError("Readiness response did not identify a ready Orders API.")
            return url
        except HttpError as error:
            if error.status not in TRANSIENT_HTTP:
                raise
            last_error = error
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            last_error = error
        time.sleep(5)
    raise DeploymentError(f"Public API did not become ready within {seconds}s: {last_error}")


def validate_order(order):
    if (not isinstance(order, dict)
            or type(order.get("orderId")) is not int
            or not isinstance(order.get("customerId"), str) or not order["customerId"]
            or not isinstance(order.get("productId"), str) or not order["productId"]
            or type(order.get("quantity")) is not int or not 1 <= order["quantity"] <= 1000
            or type(order.get("unitPrice")) not in (int, float) or not 0 < order["unitPrice"] < 1000000
            or not isinstance(order.get("createdUtc"), str)):
        raise DeploymentError("Orders API returned an invalid persisted order.")
    try:
        datetime.fromisoformat(order["createdUtc"].replace("Z", "+00:00"))
    except ValueError as error:
        raise DeploymentError("Orders API returned an invalid order timestamp.") from error


def smoke(values):
    url = await_ready(values)
    status, orders = request_json(url, "/orders")
    if status != 200 or not isinstance(orders, list) or len(orders) < 5:
        raise DeploymentError("Public /orders must return at least five initialized orders.")
    for order in orders:
        validate_order(order)
    for path in ("/fault/cpu", "/fault/storage", "/fault/reset"):
        try:
            request_json(url, path, "POST", {})
        except HttpError as error:
            if error.status == 404:
                continue
            raise
        raise DeploymentError(f"Destructive HTTP fault route unexpectedly exists: {path}")
    result = {"ok": True, "url": url, "ordersValidated": len(orders),
              "httpFaultRoutes": "absent", "checkedUtc": datetime.now(timezone.utc).isoformat()}
    write_json(state_directory(values) / "smoke.json", result)
    progress(f"[smoke] Read {len(orders)} persisted orders from {url}/orders; HTTP fault routes are absent.")
    return result


def postprovision():
    values = environment()
    started = state_directory(values) / "provision-start.json"
    if started.exists():
        seconds = time.time() - json.loads(started.read_text(encoding="utf-8"))["started"]
        record_stage(values, "infrastructure", seconds)
        progress(f"[infrastructure] Completed in {seconds:.1f}s")
    with stage(values, "vm-configuration"):
        deploy_vm(values)
    with stage(values, "sre-agent-configuration"):
        ensure_sre_response_plan(values)
    with stage(values, "public-smoke"):
        smoke(values)
    export_values(values)
    progress(f"Orders API: {required(values, 'SERVICE_ORDERS_API_ENDPOINT_URL')}")
    for key in ("VM_RESOURCE_ID", "DATA_DISK_RESOURCE_ID", "LOG_ANALYTICS_ID", "SRE_AGENT_RESOURCE_ID"):
        progress(f"{key}: {required(values, key)}")


def inspect_vm(values):
    result = run_vm(values, "/usr/bin/python3 /opt/orders-api/inspect.py '__WORKSHOP_REQUEST_ID__'")
    write_json(state_directory(values) / "vm-inspection.json", result)
    return result


def fault(values, action, arguments):
    fields = {
        "cpu": ((300, 2), ((10, 1800), (1, 8))),
        "disk": ((90, 300), ((50, 97), (30, 1800))),
        "status": ((), ()), "reset": ((), ()),
    }
    action = {"storage": "disk", "release": "reset"}.get(action, action)
    if action not in fields:
        raise DeploymentError("Fault action must be cpu, disk, status, or reset.")
    defaults, limits = fields[action]
    if len(arguments) > len(defaults):
        raise DeploymentError(f"Too many arguments for {action}.")
    try:
        numbers = [int(value) for value in arguments] + list(defaults[len(arguments):])
    except (ValueError, TypeError) as error:
        raise DeploymentError("Fault arguments must be integers.") from error
    for number, (minimum, maximum) in zip(numbers, limits):
        if not minimum <= number <= maximum:
            raise DeploymentError(f"{action} argument must be between {minimum} and {maximum}.")
    command = "/usr/bin/python3 /opt/orders-api/faults.py " + " ".join(
        [action, *map(str, numbers), "--request-id", "__WORKSHOP_REQUEST_ID__"]
    )
    progress(f"[fault-{action}] Invoking authenticated Azure VM Run Command")
    result = run_vm(values, command, timeout=240)
    write_json(state_directory(values) / f"fault-{action}.json", result)
    return result


def query_logs(values, query):
    workspace = required(values, "LOG_ANALYTICS_CUSTOMER_ID")
    try:
        uuid.UUID(workspace)
    except ValueError as error:
        raise DeploymentError("Invalid Log Analytics workspace identifier.") from error
    with tempfile.TemporaryDirectory(prefix="sre-logs-") as temporary:
        path = Path(temporary) / "query.json"
        write_json(path, {"query": query, "timespan": "PT1H"})
        response = az(
            "rest", "--method", "post", "--url",
            f"https://api.loganalytics.io/v1/workspaces/{workspace}/query",
            "--resource", "https://api.loganalytics.io", "--body", "@" + str(path),
        )
    if not isinstance(response, dict) or response.get("error"):
        raise DeploymentError("Log Analytics returned a query error.")
    tables = response.get("tables")
    if not isinstance(tables, list) or len(tables) != 1:
        raise DeploymentError("Unexpected Log Analytics query result.")
    table = tables[0]
    columns = [column["name"] for column in table["columns"]]
    return [dict(zip(columns, row)) for row in table["rows"]]


def telemetry(values, timeout=600):
    vm_id = required(values, "VM_RESOURCE_ID")
    if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]+/resourceGroups/[\w.()-]+/"
                        r"providers/Microsoft.Compute/virtualMachines/[\w-]+", vm_id):
        raise DeploymentError("Unexpected VM resource identifier.")
    query = f"""
union isfuzzy=true
    (datatable(Signal:string, Samples:long)[]),
    (Heartbeat | where _ResourceId =~ '{vm_id}' | summarize Samples=count() | extend Signal='heartbeat'),
    (Perf | where _ResourceId =~ '{vm_id}' and CounterName == '% Processor Time'
        | summarize Samples=count() | extend Signal='cpu'),
    (Perf | where _ResourceId =~ '{vm_id}' and CounterName == '% Free Space'
        and InstanceName contains '/var/lib/orders'
        | summarize Samples=count() | extend Signal='data-disk'),
    (AppRequests | where AppRoleName == 'orders-api' | summarize Samples=count() | extend Signal='requests'),
    (AppDependencies | where AppRoleName == 'orders-api' | summarize Samples=count() | extend Signal='dependencies'),
    (AppAvailabilityResults | where AppRoleName == 'orders-api'
        | summarize Samples=count() | extend Signal='availability'),
    (AppExceptions | where AppRoleName == 'orders-api' | summarize Samples=count() | extend Signal='exceptions')
| project Signal, Samples
"""
    expected = {"heartbeat", "cpu", "data-disk", "requests", "dependencies", "availability"}
    deadline = time.monotonic() + timeout
    counts = {}
    while time.monotonic() < deadline:
        counts = {row["Signal"]: row["Samples"] for row in query_logs(values, query)}
        missing = {signal for signal in expected if counts.get(signal, 0) < 1}
        if not missing:
            result = {"ok": True, "samples": counts, "checkedUtc": datetime.now(timezone.utc).isoformat()}
            write_json(state_directory(values) / "telemetry.json", result)
            return result
        progress("[telemetry] Waiting for ingestion: " + ", ".join(sorted(missing)))
        time.sleep(15)
    raise DeploymentError(f"Telemetry did not arrive within {timeout}s. Observed samples: {counts}")


def verify_restart(values):
    url = await_ready(values)
    before = inspect_vm(values)
    disk_uuid = before["storage"]["uuid"]
    witness_path = state_directory(values) / "persistence-witness.json"
    witness = None
    if witness_path.exists():
        existing = json.loads(witness_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise DeploymentError("Persistence witness is invalid.")
        if "diskUuid" not in existing:
            progress("[restart] Replacing a legacy persistence witness without disk identity.")
        elif existing["diskUuid"] != disk_uuid:
            raise DeploymentError(
                "Persistence witness belongs to a different managed data disk. "
                "Fresh-environment evidence should be cleared before validation."
            )
        else:
            witness = existing
    if witness is not None:
        _, order = request_json(url, f"/orders/{witness['orderId']}")
        if order != witness["order"]:
            raise DeploymentError("Previously recorded persistence witness changed or was lost.")
    else:
        status, created = request_json(url, "/orders", "POST", {
            "customerId": "vm-persistence-" + uuid.uuid4().hex,
            "productId": "SKU-1001", "quantity": 3,
        })
        if status != 201 or type(created.get("orderId")) is not int or created["orderId"] <= 0:
            raise DeploymentError("Persistence test could not create a positive-ID order.")
        _, order = request_json(url, f"/orders/{created['orderId']}")
        validate_order(order)
        witness = {"orderId": created["orderId"], "order": order, "diskUuid": disk_uuid}
        write_json(witness_path, witness)
    progress("[restart] Restarting only the selected workshop VM through Azure")
    az("vm", "restart", "--resource-group", required(values, "RESOURCE_GROUP"),
       "--name", required(values, "VM_NAME"),
       "--subscription", required(values, "AZURE_SUBSCRIPTION_ID"), timeout=300)
    await_ready(values, seconds=180)
    after = inspect_vm(values)
    _, persisted = request_json(url, f"/orders/{witness['orderId']}")
    if persisted != witness["order"]:
        raise DeploymentError("The public order changed or disappeared after VM restart.")
    if before["bootId"] == after["bootId"]:
        raise DeploymentError("VM restart did not change the boot ID.")
    if before["storage"]["uuid"] != after["storage"]["uuid"]:
        raise DeploymentError("The managed data disk changed after restart.")
    result = {"ok": True, "orderId": witness["orderId"], "beforeBootId": before["bootId"],
              "afterBootId": after["bootId"], "diskUuid": after["storage"]["uuid"]}
    write_json(state_directory(values) / "restart.json", result)
    smoke(values)
    return result


def benchmark():
    values = environment()
    check_resource_group(values, fresh=True)
    location = required(values, "AZURE_LOCATION")
    if location != "australiaeast":
        raise DeploymentError("The fresh benchmark must deploy to australiaeast.")
    executable = shutil.which("azd")
    if not executable:
        raise DeploymentError("Install azd before benchmarking.")
    run = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = state_directory(values) / run
    directory.mkdir()
    progress(f"[benchmark] Full azd up output: {directory / 'azd-up.log'}")
    started_utc = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    process_env = os.environ.copy()
    process_env["AZD_SKIP_UPDATE_CHECK"] = "true"
    with (directory / "azd-up.log").open("w", encoding="utf-8") as log:
        with subprocess.Popen(
            [executable, "up", "--no-prompt", "--environment", values["AZURE_ENV_NAME"]],
            cwd=ROOT, env=process_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        ) as process:
            for line in process.stdout:
                log.write(line)
                log.flush()
                print(line, end="", flush=True)
            code = process.wait()
    elapsed = time.perf_counter() - started
    result = {"environment": values["AZURE_ENV_NAME"], "location": location,
              "startedUtc": started_utc, "seconds": round(elapsed, 3), "exitCode": code,
              "freshResourceGroup": True, "targetSeconds": [480, 900],
              "within15Minutes": code == 0 and elapsed <= 900}
    report = state_directory(values) / "deployment.json"
    if report.exists():
        result["deployment"] = json.loads(report.read_text(encoding="utf-8"))
    write_json(directory / "benchmark.json", result)
    write_json(state_directory(values) / "fresh-benchmark.json", result)
    progress(f"[benchmark] azd up including all smoke checks: {elapsed:.1f}s ({elapsed / 60:.2f} minutes)")
    if code:
        raise DeploymentError(f"Fresh azd up failed (exit {code}); see {directory / 'azd-up.log'}.")
    if not (state_directory(values) / "smoke.json").exists():
        raise DeploymentError("azd up returned success without live smoke evidence.")
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=(
        "validate", "prepare", "preprovision", "postprovision", "export",
        "smoke", "inspect", "fault", "telemetry", "verify-restart", "benchmark",
    ))
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    direct = {"validate": validate, "prepare": prepare, "preprovision": preprovision,
              "postprovision": postprovision, "benchmark": benchmark}
    if args.command in direct:
        if args.arguments:
            raise DeploymentError(f"{args.command} does not accept positional arguments.")
        direct[args.command]()
        return
    values = environment()
    if args.command == "fault":
        result = fault(values, args.arguments[0] if args.arguments else "status", args.arguments[1:])
    else:
        if args.arguments:
            raise DeploymentError(f"{args.command} does not accept positional arguments.")
        operations = {"export": export_values, "smoke": smoke, "inspect": inspect_vm,
                      "telemetry": telemetry, "verify-restart": verify_restart}
        result = operations[args.command](values)
    if result is not None:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (DeploymentError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
