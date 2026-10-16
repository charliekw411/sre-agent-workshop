#!/usr/bin/env python3
"""Cross-platform deployment, bounded faults, and opt-in incident and health-check workflows."""

import argparse
import base64
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import fnmatch
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
    "Microsoft.DBforPostgreSQL",
}
SAFE_OUTPUTS = {
    "AZURE_ENV_NAME", "AZURE_LOCATION", "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP", "RESOURCE_GROUP", "SUBSCRIPTION_ID", "TENANT_ID",
    "WORKSHOP_SUFFIX", "LOCATION", "VM_NAME", "VM_RESOURCE_ID",
    "PUBLIC_IP_ADDRESS", "PUBLIC_IP_RESOURCE_ID", "ORDERS_API_FQDN",
    "SERVICE_ORDERS_API_ENDPOINT_URL", "NETWORK_SECURITY_GROUP_NAME",
    "NETWORK_SECURITY_GROUP_RESOURCE_ID", "POSTGRESQL_SUBNET_RESOURCE_ID",
    "POSTGRESQL_FAULT_RULE_NAME", "POSTGRESQL_FAULT_RULE_RESOURCE_ID",
    "POSTGRESQL_SERVER_NAME", "POSTGRESQL_SERVER_RESOURCE_ID", "POSTGRESQL_HOST",
    "POSTGRESQL_DATABASE", "POSTGRESQL_USER", "POSTGRESQL_PRIVATE_DNS_ZONE_NAME",
    "POSTGRESQL_PRIVATE_DNS_ZONE_RESOURCE_ID",
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
MONITORING_CONTRIBUTOR_ROLE_ID = "749f88d5-cbae-40b8-bcfc-e573ddc772fa"
PUBLIC_IP_FEATURE = "AllowBringYourOwnPublicIpAddress"
SRE_ARM_API_VERSION = "2025-05-01-preview"
AGENT_DEFINITIONS = ROOT / "agent"
HEALTH_CHECK_TEMPLATE = AGENT_DEFINITIONS / "scheduled-tasks" / "orders-daily-health-report.yaml"
HEALTH_CHECK_EVIDENCE = "scheduled-health-check.json"
HEALTH_CHECK_ACTIONS = ("install", "status", "remove")
KNOWLEDGE_EVIDENCE = "knowledge-base.json"
KNOWLEDGE_ACTIONS = ("install", "status", "remove")
KNOWLEDGE_BASE = "/api/v1/AgentMemory/"
KNOWLEDGE_NAME = re.compile(r"workshop-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
KNOWLEDGE_MAX_BYTES = 16 * 1024 * 1024
# Only these tools can ever be attached to the health-check custom agent.
HEALTH_CHECK_TOOLS = frozenset({
    "GetAzCliHelp", "RunAzCliReadCommands",
    "PlotAreaChartWithCorrelation", "PlotBarChart",
})
# The installer adds the query tool for each healthy telemetry connector.
HEALTH_CONNECTOR_TOOLS = {
    "LogAnalytics": "QueryLogAnalyticsByWorkspaceId",
    "AppInsights": "QueryAppInsightsUsingAppId",
}
INCIDENT_WORKFLOW_NAME = "orders-incident-workflow"
INCIDENT_WORKFLOW_AGENT = "orders-alert-investigator"
INCIDENT_WORKFLOW_TEMPLATE = AGENT_DEFINITIONS / "workflows" / "orders-incident-workflow.yaml"
INCIDENT_WORKFLOW_STATE = "incident-workflow-state.json"
INCIDENT_WORKFLOW_EVIDENCE = "incident-workflow.json"
INCIDENT_WORKFLOW_ACTIONS = ("install", "status", "remove")
INCIDENT_SOURCE_TOOLS = {"ReadFile", "ListDir", "FileSearch", "GrepSearch"}
INCIDENT_GITHUB_TOOLS = {"FetchGithubIssue", "FetchGithubIssues", "CreateGithubIssue"}
INCIDENT_EMAIL_TOOLS = {"ListOutlookEmails", "SendOutlookEmail"}
INCIDENT_APPROVAL_TOOLS = {"CreateGithubIssue", "SendOutlookEmail"}
# The autonomous investigator's only Azure write is the bounded NSG rule restore;
# its instructions confine this tool to that one change.
INCIDENT_WRITE_TOOLS = {"RunAzCliWriteCommands"}
INCIDENT_MODE = "Autonomous"
INCIDENT_READ_TOOLS = (
    HEALTH_CHECK_TOOLS | INCIDENT_SOURCE_TOOLS
    | (INCIDENT_GITHUB_TOOLS | INCIDENT_EMAIL_TOOLS) - INCIDENT_APPROVAL_TOOLS
)
INCIDENT_SKILLS = (
    "orders-azure-monitor-rca", "orders-github-issue-followup",
    "orders-email-incident-followup",
)
INCIDENT_ALERT_RULES = (
    "alert-orders-postgresql-connectivity",
)
INCIDENT_DEFAULT_TITLE = "alert-orders-h"
INCIDENT_WORKFLOW_TITLE = "postgresql"
INCIDENT_BASE = "/api/v2/extendedAgent/"
INCIDENT_PLAN_READ_ONLY_PROPERTIES = {"createdAt", "updatedAt", "configurationWarning"}
FRESH_ENVIRONMENT_EVIDENCE = (
    "provision-start.json",
    "vm-deployment.json",
    "sre-agent-configuration.json",
    HEALTH_CHECK_EVIDENCE,
    KNOWLEDGE_EVIDENCE,
    INCIDENT_WORKFLOW_STATE,
    INCIDENT_WORKFLOW_EVIDENCE,
    "smoke.json",
    "vm-inspection.json",
    "persistence-witness.json",
    "restart.json",
    "telemetry.json",
    "fault-cpu.json",
    "fault-reset-cpu.json",
    "fault-postgresql.json",
    "fault-postgresql-partial.json",
    "fault-reset-postgresql.json",
    "fault-reset-postgresql-partial.json",
    "fault-status.json",
    "fault-reset.json",
    "fault-reset-partial.json",
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
        if sys.platform == "win32" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
            runtime = Path(executable).parent.parent / "python.exe"
            if not runtime.is_file():
                raise DeploymentError(
                    "The Windows Azure CLI launcher has no bundled Python runtime; "
                    "use the official Azure CLI installation to preserve literal arguments.")
            command = [str(runtime), "-I", "-B", "-m", "azure.cli", *args]
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


def monitoring_contributor_assignments(values):
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    principal = required(values, "SRE_AGENT_IDENTITY_PRINCIPAL_ID")
    try:
        uuid.UUID(subscription)
        uuid.UUID(principal)
    except ValueError as error:
        raise DeploymentError(
            "The SRE Agent subscription and UAMI principal IDs must be GUIDs."
        ) from error
    scope = f"/subscriptions/{subscription}"
    role_definition = (
        f"{scope}/providers/Microsoft.Authorization/roleDefinitions/"
        f"{MONITORING_CONTRIBUTOR_ROLE_ID}"
    )
    assignments = az(
        "role", "assignment", "list",
        "--assignee-object-id", principal,
        "--scope", scope,
        "--fill-principal-name", "false",
        "--subscription", subscription,
    )
    if not isinstance(assignments, list):
        raise DeploymentError(
            "Azure returned an invalid Monitoring Contributor assignment list.")
    matches = [
        assignment for assignment in assignments
        if isinstance(assignment, dict)
        and str(assignment.get("scope", "")).casefold() == scope.casefold()
        and str(assignment.get("roleDefinitionId", "")).casefold()
        == role_definition.casefold()
    ]
    if len(matches) > 1:
        raise DeploymentError(
            "The SRE Agent UAMI has duplicate Monitoring Contributor assignments.")
    return matches


def remove_monitoring_contributor(values):
    assignments = monitoring_contributor_assignments(values)
    if not assignments:
        progress("[cleanup] SRE Agent Monitoring Contributor assignment is already absent.")
        return
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    scope = f"/subscriptions/{subscription}"
    assignment_id = assignments[0].get("id")
    if (not isinstance(assignment_id, str)
            or not re.fullmatch(
                re.escape(scope)
                + r"/providers/Microsoft\.Authorization/roleAssignments/"
                + r"[0-9a-fA-F-]{36}",
                assignment_id,
            )):
        raise DeploymentError(
            "Azure returned an invalid Monitoring Contributor assignment ID.")
    az(
        "role", "assignment", "delete",
        "--ids", assignment_id,
        "--subscription", subscription,
    )
    for _ in range(6):
        if not monitoring_contributor_assignments(values):
            progress("[cleanup] Removed the SRE Agent Monitoring Contributor assignment.")
            return
        time.sleep(5)
    raise DeploymentError(
        "The SRE Agent Monitoring Contributor assignment is still visible after deletion.")


def environment():
    command = ["env", "get-values", "--output", "json"]
    selected = os.environ.get("AZURE_ENV_NAME")
    if selected:
        command += ["--environment", selected]
    values = json.loads(cli("azd", *command))
    if not isinstance(values, dict):
        raise DeploymentError("azd did not return an environment object.")
    env_name = required(values, "AZURE_ENV_NAME")
    if selected and env_name != selected:
        raise DeploymentError(
            f"azd returned environment {env_name}, not the requested {selected}."
        )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", env_name):
        raise DeploymentError("The azd environment name must contain only letters, digits, - and _.")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    try:
        uuid.UUID(subscription)
    except ValueError as error:
        raise DeploymentError("AZURE_SUBSCRIPTION_ID must be a subscription GUID.") from error
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
    directory = (
        state_directory(values)
        if os.environ.get("WORKSHOP_COHORT_MODE") == "1"
        else ROOT / ".workshop"
    )
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


def sre_agent_locations(subscription):
    locations = az(
        "provider", "show",
        "--namespace", "Microsoft.App",
        "--subscription", subscription,
        "--query", "resourceTypes[?resourceType=='agents'].locations | [0]",
    )
    return locations if isinstance(locations, list) else []


def register_providers(subscription=None, location=None):
    subscription_args = (
        ["--subscription", subscription] if subscription else []
    )

    def states():
        return {item["namespace"].casefold(): item["state"].casefold() for item in az(
            "provider", "list", *subscription_args,
            "--query", "[].{namespace:namespace,state:registrationState}",
        )}

    current = states()
    pending = {provider for provider in PROVIDERS
               if current.get(provider.casefold()) != "registered"}
    for provider in sorted(pending):
        if current.get(provider.casefold()) == "registering":
            progress(f"[preflight] {provider} registration is already in progress")
            continue
        progress(f"[preflight] Registering {provider}")
        az("provider", "register", "--namespace", provider, *subscription_args)
    deadline = time.monotonic() + 1800
    while pending:
        if time.monotonic() >= deadline:
            raise DeploymentError(
                "Provider registration exceeded thirty minutes: "
                + ", ".join(sorted(pending))
            )
        time.sleep(10)
        current = states()
        pending = {provider for provider in pending
                   if current.get(provider.casefold()) != "registered"}
        if ("Microsoft.App" in pending and location and subscription
                and current.get("microsoft.app") == "registering"
                and location.casefold() in {
                    item.replace(" ", "").casefold()
                    for item in sre_agent_locations(subscription)
                }):
            pending.remove("Microsoft.App")
            progress(
                f"[preflight] Microsoft.App/agents is available in {location}; "
                "provider registration can finish in the background."
            )
        if pending:
            progress("[preflight] Waiting for " + ", ".join(sorted(pending)))


def token_identity(token):
    segment = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    return str(uuid.UUID(claims["oid"])), str(uuid.UUID(claims["tid"]))


def resource_group_name(values):
    group = values.get("WORKSHOP_RESOURCE_GROUP_NAME") or (
        "rg-sre-agent-workshop-" + required(values, "AZURE_ENV_NAME")
    )
    if (not isinstance(group, str) or not 1 <= len(group) <= 90
            or not re.fullmatch(r"[A-Za-z0-9_.()\-]+", group)
            or group.endswith(".")):
        raise DeploymentError("WORKSHOP_RESOURCE_GROUP_NAME is not a valid Azure resource-group name.")
    return group


def check_resource_group(values, fresh=False):
    group = resource_group_name(values)
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
    tags = az("group", "show", "--name", group, "--subscription", subscription, "--query", "tags")
    if tags is None:
        tags = {}
    if not isinstance(tags, dict):
        raise DeploymentError("Unexpected resource group tags response.")
    if values.get("WORKSHOP_RESOURCE_GROUP_NAME") and not tags:
        resources = az("resource", "list", "--resource-group", group,
                       "--subscription", subscription, "--query", "[].id")
        if not isinstance(resources, list):
            raise DeploymentError("Unexpected resource group inventory response.")
        if not resources:
            progress(f"[preflight] Using explicitly selected empty, untagged group {group}.")
            return True
    if tags.get("workshop-architecture") != "single-vm-postgresql-v1":
        raise DeploymentError(
            f"{group} is not a compatible PostgreSQL workshop environment. "
            "Use a new azd environment or explicitly remove the old disposable "
            "environment; legacy resources and data are not migrated or deleted."
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


def check_vm_capacity(values, check_existing=True, instances=1):
    location = required(values, "AZURE_LOCATION")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    size = values.get("VM_SIZE") or "Standard_D2as_v5"
    if type(instances) is not int or instances < 1:
        raise DeploymentError("VM capacity instances must be a positive integer.")
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
        required_cpus = cpus * instances
        if available < required_cpus:
            raise DeploymentError(
                f"{instances} {size} VM(s) need {required_cpus} vCPUs, but {name} has "
                f"{available} remaining in {location}. "
                "Select an available size within existing quotas. Free Trial subscriptions cannot request quota increases."
            )
    progress(
        f"[preflight] {instances} x {size}: {required_cpus} x64 vCPUs total, "
        f"{memory:g} GiB RAM each; regional and family quotas available."
    )


def check_sre_agent_region(values):
    location = required(values, "AZURE_LOCATION")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    locations = sre_agent_locations(subscription)
    if location.casefold() not in {
            item.replace(" ", "").casefold() for item in locations}:
        raise DeploymentError(
            f"SRE Agent is not advertised in {location}; no region substitution is made."
        )
    progress(f"[preflight] Azure SRE Agent is advertised in {location}.")


def ensure_public_ip_feature(values, timeout=1800):
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")

    def state():
        feature = az(
            "feature", "show",
            "--namespace", "Microsoft.Network",
            "--name", PUBLIC_IP_FEATURE,
            "--subscription", subscription,
        )
        return str(feature.get("properties", {}).get("state", ""))

    current = state()
    if current == "Registered":
        return
    if current == "NotRegistered":
        progress(
            f"[preflight] Registering Microsoft.Network/{PUBLIC_IP_FEATURE}"
        )
        az(
            "feature", "register",
            "--namespace", "Microsoft.Network",
            "--name", PUBLIC_IP_FEATURE,
            "--subscription", subscription,
        )
    elif current not in {"Pending", "Registering"}:
        raise DeploymentError(
            f"Microsoft.Network/{PUBLIC_IP_FEATURE} has unexpected state {current!r}."
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(15)
        current = state()
        if current == "Registered":
            az(
                "provider", "register",
                "--namespace", "Microsoft.Network",
                "--subscription", subscription,
            )
            progress(
                f"[preflight] Microsoft.Network/{PUBLIC_IP_FEATURE} is registered."
            )
            return
        if current not in {"Pending", "Registering"}:
            raise DeploymentError(
                f"Microsoft.Network/{PUBLIC_IP_FEATURE} changed to "
                f"unexpected state {current!r}."
            )
        progress(
            f"[preflight] Waiting for Microsoft.Network/{PUBLIC_IP_FEATURE}"
        )
    raise DeploymentError(
        f"Microsoft.Network/{PUBLIC_IP_FEATURE} registration exceeded thirty minutes."
    )


def check_postgresql_capacity(values):
    location = required(values, "AZURE_LOCATION")
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
    capabilities = az(
        "postgres", "flexible-server", "list-skus",
        "--location", location,
        "--subscription", subscription,
        timeout=300,
    )
    if not isinstance(capabilities, list):
        raise DeploymentError(
            "Azure did not return PostgreSQL Flexible Server capabilities.")
    versions = {
        str(version.get("name"))
        for capability in capabilities if isinstance(capability, dict)
        for version in (capability.get("supportedServerVersions") or [])
        if isinstance(version, dict) and version.get("name")
    }
    skus = {
        str(sku.get("name"))
        for capability in capabilities if isinstance(capability, dict)
        for edition in (capability.get("supportedServerEditions") or [])
        if isinstance(edition, dict)
        for sku in (edition.get("supportedServerSkus") or [])
        if isinstance(sku, dict) and sku.get("name")
    }
    if "16" not in versions or "Standard_B1ms" not in skus:
        reasons = sorted({
            str(capability.get("reason"))
            for capability in capabilities
            if isinstance(capability, dict) and capability.get("reason")
        })
        detail = " ".join(reasons)
        raise DeploymentError(
            f"PostgreSQL 16 / Standard_B1ms is not advertised in {location} "
            f"for this subscription. {detail} No resources were deployed.".strip()
        )
    progress(
        f"[preflight] PostgreSQL 16 / Standard_B1ms is advertised in {location}.")


def validate():
    config = yaml.safe_load((ROOT / "azure.yaml").read_text(encoding="utf-8"))
    if config.get("services"):
        raise DeploymentError("This VM deployment must not declare Container Apps services.")
    steps = config.get("workflows", {}).get("up", {}).get("steps")
    if steps != [{"azd": "provision"}]:
        raise DeploymentError("azd up must provision and run the postprovision VM deployment hook.")
    for hook in ("preup", "preprovision", "postprovision", "predown"):
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
    for filename in (
        "postgresql.bicep", "postgresql-identity.bicep", "fault.bicep",
    ):
        if not (ROOT / "infra" / filename).is_file():
            raise DeploymentError(
                f"PostgreSQL infrastructure file is missing: {filename}")
    progress("VM, PostgreSQL, and fault deployment source files are valid.")


def prepare():
    values = environment()
    subscription = required(values, "AZURE_SUBSCRIPTION_ID")
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
                   "https://management.azure.com/",
                   "--subscription", subscription,
                   sensitive=True)["accessToken"]
        principal, tenant = token_identity(token)
        azd_token = json.loads(cli(
            "azd", "auth", "token", "--scope", "https://management.azure.com/.default",
            "--tenant-id", tenant, "--output", "json", sensitive=True,
        ))["token"]
        if token_identity(token) != token_identity(azd_token):
            raise DeploymentError("Azure CLI and azd must use the same principal and tenant.")
        account = az("account", "show", "--subscription", subscription)
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
            register_providers(
                subscription, required(values, "AZURE_LOCATION"),
            )
            ensure_public_ip_feature(values)
            check_sre_agent_region(values)
            check_vm_capacity(values, check_existing=False)
            check_postgresql_capacity(values)
        ensure_ssh_public_key(values)
        export_values(values)


def preprovision():
    values = environment()
    if not values.get("VM_SSH_PUBLIC_KEY"):
        raise DeploymentError("Run azd up first so preup can prepare the VM parameters.")
    check_resource_group(values)
    write_json(state_directory(values) / "provision-start.json", {"started": time.time()})
    progress(
        "[infrastructure] Provisioning VM, private PostgreSQL, monitoring, and SRE Agent")


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
        "postgresqlHost": required(values, "POSTGRESQL_HOST"),
        "postgresqlDatabase": required(values, "POSTGRESQL_DATABASE"),
        "postgresqlUser": required(values, "POSTGRESQL_USER"),
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


def sre_agent_token(values):
    response = az(
        "account", "get-access-token", "--resource", "https://azuresre.dev",
        "--subscription", required(values, "AZURE_SUBSCRIPTION_ID"),
        sensitive=True,
    )
    token = response.get("accessToken") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise DeploymentError("Azure CLI did not return an Azure SRE Agent access token.")
    return token


def sre_agent_multipart(fields, file_field, filename, content_type, content):
    boundary = "workshop" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
            .encode("utf-8"))
    parts.append(
        (f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
         f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode("utf-8"))
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def sre_agent_request(endpoint, token, path, method="GET", body=None, *,
                      if_match=None, with_etag=False, multipart=None, lenient=False):
    content_type = "application/json"
    if multipart is not None:
        data, content_type = sre_agent_multipart(*multipart)
    else:
        data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        endpoint + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Content-Type": content_type,
        },
    )
    if if_match is not None:
        if not isinstance(if_match, str) or not re.fullmatch(r'"[\x21\x23-\x7e]+"', if_match):
            raise DeploymentError("A conditional SRE Agent write requires a strong ETag.")
        request.add_header("If-Match", if_match)
    try:
        with urllib.request.build_opener(SreNoRedirect).open(request, timeout=20) as response:
            payload = response.read(128 * 1024 + 1)
            if len(payload) > 128 * 1024:
                raise DeploymentError("Azure SRE Agent response exceeded the expected size.")
            try:
                document = json.loads(payload) if payload else None
            except ValueError:
                if not lenient:
                    raise
                document = None
            if not with_etag:
                return document
            etags = response.headers.get_all("ETag") or []
            if (len(etags) > 1 or etags and not re.fullmatch(r'"[\x21\x23-\x7e]+"', etags[0])):
                raise DeploymentError("Azure SRE Agent returned an unusable ETag.")
            return document, etags[0] if etags else None
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
    token = sre_agent_token(values)
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
            listing = sre_agent_request(endpoint, token, "/api/v2/extendedAgent/incidentFilters")
            plans = sre_collection(listing, "response-plan")
            if (incident_state_path(values).exists()
                    or any(item.get("name") == INCIDENT_WORKFLOW_NAME for item in plans)):
                report = incident_workflow_status(values)
                if not report["matchesDefinition"]:
                    raise DeploymentError(
                        "The incident workflow needs attention; provisioning will not restore "
                        "an overlapping catch-all. Run incident-workflow status, then remove "
                        "or reconcile the module. " + " ".join(report["problems"]))
                result = {
                    "ok": True, "name": SRE_RESPONSE_PLAN_NAME,
                    "incidentPlatform": "AzMonitor", "priorities": ["Sev1", "Sev2"],
                    "mode": "Review", "handlingAgent": "meta_agent",
                    "titleContains": report["routing"]["originalTitleContains"],
                    "incidentWorkflow": INCIDENT_WORKFLOW_NAME,
                }
                write_json(state_directory(values) / "sre-agent-configuration.json", result)
                progress("[sre-agent] Preserved both complementary incident response plans.")
                return result
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


def health_check_name(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", value):
        raise DeploymentError(f"{label} must contain only letters, digits, and hyphens.")
    return value


def health_check_text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"{label} must be a non-empty string.")
    return value.strip()


def health_check_mapping(value, label):
    if not isinstance(value, dict):
        raise DeploymentError(f"{label} must be a mapping.")
    return value


def health_check_tools(value, label):
    if (not isinstance(value, list) or not value
            or not all(isinstance(tool, str) and re.fullmatch(r"[A-Za-z0-9]+", tool)
                       for tool in value)):
        raise DeploymentError(f"{label} must be a non-empty list of tool names.")
    return list(dict.fromkeys(value))


def read_health_check_skill(path, name):
    definitions = AGENT_DEFINITIONS.resolve()
    resolved = path.resolve()
    if definitions not in resolved.parents or not resolved.is_file():
        raise DeploymentError(f"Skill {name} must be a file under {definitions.name}/.")
    text = resolved.read_text(encoding="utf-8")
    match = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    try:
        metadata = yaml.safe_load(match.group(1)) if match else None
    except yaml.YAMLError as error:
        raise DeploymentError(f"Skill {name} has invalid YAML frontmatter.") from error
    if not isinstance(metadata, dict) or metadata.get("name") != name:
        raise DeploymentError(f"Skill {name} needs YAML frontmatter with a matching name.")
    description = health_check_text(
        metadata.get("description"), f"The description of skill {name}")
    return {"name": name, "description": description, "content": text}


def load_health_check_definition(template=None):
    template = Path(template or HEALTH_CHECK_TEMPLATE)
    document = health_check_mapping(
        yaml.safe_load(template.read_text(encoding="utf-8")), "The health-check template")
    trigger = health_check_mapping(document.get("trigger"), "trigger")
    agent = health_check_mapping(document.get("custom_agent"), "custom_agent")
    if trigger.get("type") != "scheduled-task":
        raise DeploymentError("trigger.type must be scheduled-task.")
    if trigger.get("enabled") is not True:
        raise DeploymentError("trigger.enabled must be true.")
    if trigger.get("action_mode") != "Review" or agent.get("action_mode") != "Review":
        raise DeploymentError("The health check must run in Review mode.")
    schedule = health_check_text(trigger.get("schedule"), "trigger.schedule")
    fields = schedule.split()
    if len(fields) != 5 or not all(re.fullmatch(r"[0-9*/,-]+", field) for field in fields):
        raise DeploymentError("trigger.schedule must be a five-field UTC cron expression.")
    agent_name = health_check_name(agent.get("name"), "custom_agent.name")
    if health_check_name(trigger.get("handling_agent"), "trigger.handling_agent") != agent_name:
        raise DeploymentError("trigger.handling_agent must match custom_agent.name.")
    tools = health_check_mapping(agent.get("tools"), "custom_agent.tools")
    if set(tools) - {"read_only", "deny"}:
        raise DeploymentError("custom_agent.tools allows only read_only and deny.")
    read_only = health_check_tools(tools.get("read_only"), "custom_agent.tools.read_only")
    denied = health_check_tools(tools.get("deny"), "custom_agent.tools.deny")
    unapproved = sorted(set(read_only) - HEALTH_CHECK_TOOLS)
    if unapproved:
        raise DeploymentError(
            "Only approved read-only tools can be attached to the health-check agent: "
            + ", ".join(unapproved) + ".")
    if "RunAzCliWriteCommands" not in denied or set(read_only) & set(denied):
        raise DeploymentError("RunAzCliWriteCommands must be denied and no denied tool can be attached.")
    entries = agent.get("skills")
    if not isinstance(entries, list) or not entries:
        raise DeploymentError("custom_agent.skills must be a non-empty list.")
    skills = []
    for entry in entries:
        entry = health_check_mapping(entry, "A custom_agent.skills entry")
        name = health_check_name(entry.get("name"), "A skill name")
        source = health_check_text(entry.get("source"), f"The source of skill {name}")
        skills.append(read_health_check_skill(template.parent / source, name))
    return {
        "name": health_check_name(document.get("name"), "name"),
        "description": health_check_text(trigger.get("description"), "trigger.description"),
        "schedule": " ".join(fields),
        "prompt": health_check_text(trigger.get("prompt"), "trigger.prompt"),
        "agent": agent_name,
        "handoffDescription": health_check_text(
            agent.get("handoff_description"), "custom_agent.handoff_description"),
        "instructions": health_check_text(agent.get("instructions"), "custom_agent.instructions"),
        "tools": read_only,
        "skills": skills,
    }


def skill_agent_documents(definition, telemetry_tools=()):
    tools = list(dict.fromkeys([*definition["tools"], *telemetry_tools]))
    skills = [{
        "name": skill["name"], "type": "Skill", "tags": [],
        "properties": {
            "name": skill["name"], "description": skill["description"], "tools": [],
            "skillContent": skill["content"], "additionalFiles": [],
        },
    } for skill in definition["skills"]]
    agent = {
        "name": definition["agent"], "type": "ExtendedAgent", "tags": [],
        "properties": {
            "instructions": definition["instructions"],
            "handoffDescription": definition["handoffDescription"],
            "handoffs": [], "tools": tools,
            # Pin optional categories; verify actual runtime tools separately.
            "mcpTools": [], "connectors": [], "agentsAsTools": [], "commonTools": [],
            "enableVanillaMode": False, "temperature": 0.2,
            "enableSkills": True, "addSystemSkills": False,
            "allowedSkills": [skill["name"] for skill in definition["skills"]],
        },
    }
    return skills, agent


def health_check_documents(definition, telemetry_tools=()):
    skills, agent = skill_agent_documents(definition, telemetry_tools)
    task = {
        "name": definition["name"], "type": "ScheduledTask", "tags": [],
        "properties": {
            "name": definition["name"], "description": definition["description"],
            "cronExpression": definition["schedule"], "agentPrompt": definition["prompt"],
            "agent": definition["agent"], "agentMode": "Review", "isEnabled": True,
        },
    }
    return skills, agent, task


def health_check_telemetry_tools(values):
    agent_id = required(values, "SRE_AGENT_RESOURCE_ID")
    if not re.fullmatch(
            r"/subscriptions/[0-9a-fA-F-]+/resourceGroups/[\w.()-]+/"
            r"providers/Microsoft\.App/agents/[\w-]+", agent_id):
        raise DeploymentError("Unexpected SRE Agent resource identifier.")
    response = az(
        "rest", "--method", "get", "--url",
        f"https://management.azure.com{agent_id}/DataConnectors"
        f"?api-version={SRE_ARM_API_VERSION}",
    )
    connectors = response.get("value") if isinstance(response, dict) else None
    if not isinstance(connectors, list):
        raise DeploymentError("Azure returned an invalid SRE Agent connector collection.")
    healthy = set()
    for item in connectors:
        properties = item.get("properties") if isinstance(item, dict) else None
        if (isinstance(properties, dict)
                and properties.get("provisioningState") in {"Succeeded", "Running"}):
            healthy.add(properties.get("dataConnectorType"))
    if "LogAnalytics" not in healthy:
        raise DeploymentError(
            "The log-analytics connector is not healthy. The health check queries the App*, "
            "Heartbeat, and Perf tables through it; open the agent's Connectors page, "
            "wait for it to report healthy, and run the command again.")
    return [tool for kind, tool in HEALTH_CONNECTOR_TOOLS.items() if kind in healthy]


def health_check_state(endpoint, token, definition):
    def read(path):
        try:
            return sre_agent_request(endpoint, token, path)
        except SreAgentRequestError as error:
            if error.status == 404:
                return None
            raise

    skills = {
        skill["name"]: read(f"/api/v2/extendedAgent/skills/{skill['name']}")
        for skill in definition["skills"]
    }
    agent = read(f"/api/v2/extendedAgent/agents/{definition['agent']}")
    listing = read("/api/v2/extendedAgent/scheduledtasks")
    tasks = listing.get("value", []) if isinstance(listing, dict) else (listing or [])
    if not isinstance(tasks, list):
        raise DeploymentError("Azure SRE Agent returned an invalid scheduled-task collection.")
    task = next((item for item in tasks
                 if isinstance(item, dict) and item.get("name") == definition["name"]), None)
    return {"skills": skills, "agent": agent, "task": task}


def health_check_text_matches(actual, expected):
    # Text the service does not return cannot be compared, so it is not a difference.
    if not isinstance(actual, str):
        return True

    def normalized(text):
        return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").strip().split("\n"))

    return normalized(actual) == normalized(expected)


def health_check_skill_matches(item, skill):
    properties = item.get("properties") if isinstance(item, dict) else None
    properties = properties if isinstance(properties, dict) else {}
    actual = next((properties[key] for key in ("skillContent", "skillMdContent")
                   if isinstance(properties.get(key), str)), None)

    def body(text):
        frontmatter = re.match(r"\A---\s*\n.*?\n---\s*\n", text, re.DOTALL)
        return text[frontmatter.end():] if frontmatter else text

    return actual is None or health_check_text_matches(body(actual), body(skill["content"]))


def health_check_report(state, definition, expected_tools):
    expected_skills = {skill["name"]: skill for skill in definition["skills"]}
    skills = {
        name: isinstance(item, dict) and item.get("name") == name
        for name, item in state["skills"].items()
    }
    problems = []
    for name, present in skills.items():
        if not present:
            problems.append(f"Skill {name} is not installed.")
        elif not health_check_skill_matches(state["skills"][name], expected_skills[name]):
            problems.append(f"Skill {name} content differs from the definition.")
    agent = state["agent"]
    agent_properties = agent.get("properties") if isinstance(agent, dict) else None
    custom_agent = None
    if not isinstance(agent_properties, dict) or agent.get("name") != definition["agent"]:
        problems.append(f"Custom agent {definition['agent']} is not installed.")
    else:
        attached = agent_properties.get("tools")
        attached = [tool for tool in attached if isinstance(tool, str)] if isinstance(attached, list) else []
        custom_agent = {"name": definition["agent"], "tools": sorted(attached)}
        missing = sorted(set(expected_tools) - set(attached))
        if missing:
            problems.append("Custom agent is missing tools: " + ", ".join(missing) + ".")
        approved = HEALTH_CHECK_TOOLS | set(HEALTH_CONNECTOR_TOOLS.values())
        unapproved = sorted(set(attached) - approved)
        if unapproved:
            problems.append(
                "Custom agent has tools outside the read-only set: " + ", ".join(unapproved) + ".")
        for key in ("mcpTools", "connectors", "agentsAsTools", "commonTools"):
            if agent_properties.get(key):
                problems.append(f"Custom agent property {key} is not empty.")
        if agent_properties.get("addSystemSkills"):
            problems.append("Custom agent property addSystemSkills is enabled.")
        for key, expected in (("instructions", definition["instructions"]),
                              ("handoffDescription", definition["handoffDescription"])):
            if not health_check_text_matches(agent_properties.get(key), expected):
                problems.append(f"Custom agent property {key} differs from the definition.")
        allowed = agent_properties.get("allowedSkills")
        absent = sorted(set(skills) - set(allowed if isinstance(allowed, list) else []))
        if absent:
            problems.append("Custom agent does not allow skills: " + ", ".join(absent) + ".")
    task = state["task"]
    task_properties = task.get("properties") if isinstance(task, dict) else None
    scheduled_task = None
    if not isinstance(task_properties, dict):
        problems.append(f"Scheduled task {definition['name']} is not installed.")
    else:
        enabled = task_properties.get("isEnabled") is True or task_properties.get("status") == "Active"
        scheduled_task = {
            "name": definition["name"],
            "cronExpression": task_properties.get("cronExpression"),
            "agent": task_properties.get("agent"),
            "agentMode": task_properties.get("agentMode"),
            "enabled": enabled,
        }
        for key, expected in (("cronExpression", definition["schedule"]),
                              ("agent", definition["agent"]), ("agentMode", "Review")):
            if task_properties.get(key) != expected:
                problems.append(
                    f"Scheduled task {key} is {task_properties.get(key)!r}; "
                    f"the definition expects {expected!r}.")
        for key, expected in (("agentPrompt", definition["prompt"]),
                              ("description", definition["description"])):
            if not health_check_text_matches(task_properties.get(key), expected):
                problems.append(f"Scheduled task property {key} differs from the definition.")
        if not enabled:
            problems.append("Scheduled task is not enabled.")
    return {
        "installed": all(skills.values()) and custom_agent is not None and scheduled_task is not None,
        "matchesDefinition": not problems,
        "scheduledTask": scheduled_task,
        "customAgent": custom_agent,
        "skills": skills,
        "problems": problems,
        "checkedUtc": datetime.now(timezone.utc).isoformat(),
    }


def retry_sre_agent(label, action, attempts, retry_seconds, prefix="health-check"):
    for attempt in range(1, attempts + 1):
        try:
            return action()
        except SreAgentRequestError as error:
            if not error.retryable or attempt == attempts:
                raise SreAgentRequestError(
                    f"{label} failed: {error}", status=error.status, retryable=error.retryable,
                ) from error
            progress(
                f"[{prefix}] {label} is not ready; retrying in "
                f"{retry_seconds}s ({attempt}/{attempts}): {error}"
            )
            time.sleep(retry_seconds)


def ensure_health_check(values, attempts=8, retry_seconds=15):
    definition = load_health_check_definition()
    endpoint = sre_agent_url(values)
    skills, agent, task = health_check_documents(
        definition, health_check_telemetry_tools(values))
    expected_tools = agent["properties"]["tools"]
    token = sre_agent_token(values)
    base = "/api/v2/extendedAgent/"

    def install():
        for skill in skills:
            sre_agent_request(endpoint, token, f"{base}skills/{skill['name']}", "PUT", skill)
        sre_agent_request(endpoint, token, f"{base}agents/{agent['name']}", "PUT", agent)
        sre_agent_request(endpoint, token, f"{base}scheduledtasks/{task['name']}", "PUT", task)
        report = health_check_report(
            health_check_state(endpoint, token, definition), definition, expected_tools)
        if report["problems"]:
            raise SreAgentRequestError(
                "The health check is not visible yet: " + " ".join(report["problems"]),
                retryable=True,
            )
        return report

    result = {"ok": True, **retry_sre_agent("Health check install", install, attempts, retry_seconds)}
    write_json(state_directory(values) / HEALTH_CHECK_EVIDENCE, result)
    progress(
        f"[health-check] Installed {definition['name']} ({definition['schedule']} UTC, "
        "Review mode, read-only tool list).")
    return result


def health_check_status(values):
    definition = load_health_check_definition()
    state = health_check_state(sre_agent_url(values), sre_agent_token(values), definition)
    # The skill queries the App*, Heartbeat, and Perf tables, so install always attaches this tool.
    expected_tools = [*definition["tools"], HEALTH_CONNECTOR_TOOLS["LogAnalytics"]]
    return health_check_report(state, definition, expected_tools)


def remove_health_check(values, attempts=8, retry_seconds=15):
    definition = load_health_check_definition()
    endpoint = sre_agent_url(values)
    token = sre_agent_token(values)
    targets = [
        ("scheduledtasks", definition["name"]),
        ("agents", definition["agent"]),
        *(("skills", skill["name"]) for skill in definition["skills"]),
    ]

    def remove():
        for kind, name in targets:
            try:
                sre_agent_request(endpoint, token, f"/api/v2/extendedAgent/{kind}/{name}", "DELETE")
            except SreAgentRequestError as error:
                if error.status != 404:
                    raise
        report = health_check_report(
            health_check_state(endpoint, token, definition), definition, [])
        if report["scheduledTask"] or report["customAgent"] or any(report["skills"].values()):
            raise SreAgentRequestError(
                "The health check is still visible after deletion. If this persists, delete "
                f"{definition['name']} from the agent's Scheduled tasks page.",
                retryable=True,
            )
        return report

    retry_sre_agent("Health check removal", remove, attempts, retry_seconds)
    (state_directory(values) / HEALTH_CHECK_EVIDENCE).unlink(missing_ok=True)
    progress(f"[health-check] Removed {definition['name']}, its custom agent, and its skill.")
    return {
        "ok": True,
        "removed": [name for _, name in targets],
        "checkedUtc": datetime.now(timezone.utc).isoformat(),
    }


def health_check(values, action):
    if action not in HEALTH_CHECK_ACTIONS:
        raise DeploymentError("Health-check action must be install, status, or remove.")
    return {
        "install": ensure_health_check,
        "status": health_check_status,
        "remove": remove_health_check,
    }[action](values)


def load_knowledge_documents():
    directory = AGENT_DEFINITIONS / "knowledge"
    documents = {}
    for path in sorted(directory.glob("*.md")):
        if not KNOWLEDGE_NAME.fullmatch(path.name):
            raise DeploymentError(
                f"Knowledge file {path.name} must be named workshop-<words>.md in lower case.")
        content = path.read_bytes()
        if not content.strip() or len(content) > KNOWLEDGE_MAX_BYTES:
            raise DeploymentError(f"Knowledge file {path.name} must be 1 byte to 16 MB.")
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            raise DeploymentError(f"Knowledge file {path.name} must be UTF-8 text.") from None
        documents[path.name] = content
    if not documents:
        raise DeploymentError(f"No workshop-*.md knowledge files found in {directory}.")
    return documents


def knowledge_listing(endpoint, token):
    document = sre_agent_request(endpoint, token, KNOWLEDGE_BASE + "files")
    entries = document.get("files", document.get("value")) if isinstance(document, dict) else document
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        raise DeploymentError("Azure SRE Agent returned an unexpected knowledge listing.")
    listing = {}
    for entry in entries:
        name = (entry.get("filename") or entry.get("name")) if isinstance(entry, dict) else None
        if isinstance(name, str):
            indexed = entry.get("isIndexed")
            listing[name] = {
                "indexed": indexed if isinstance(indexed, bool)
                else entry.get("indexStatus") == "indexed",
                "size": entry.get("fileSize", entry.get("size")),
            }
    return listing


def knowledge_report(listing, documents, installed=True):
    present = {name: listing[name] for name in documents if name in listing}
    problems = []
    if installed:
        problems = [f"{name} is not in the knowledge base." for name in documents if name not in present]
    return {
        "documents": {name: present.get(name) for name in documents},
        "installed": len(present) == len(documents),
        "indexed": bool(present) and all(item["indexed"] for item in present.values()),
        "problems": problems,
        "checkedUtc": datetime.now(timezone.utc).isoformat(),
    }


def ensure_knowledge(values, attempts=8, retry_seconds=15):
    documents = load_knowledge_documents()
    endpoint = sre_agent_url(values)
    token = sre_agent_token(values)

    def install():
        for name, content in documents.items():
            sre_agent_request(
                endpoint, token, KNOWLEDGE_BASE + "upload", "POST", lenient=True,
                multipart=({"triggerIndexing": "true"}, "files", name, "text/plain", content))
        report = knowledge_report(knowledge_listing(endpoint, token), documents)
        if report["problems"]:
            raise SreAgentRequestError(
                "The knowledge files are not visible yet: " + " ".join(report["problems"]),
                retryable=True,
            )
        return report

    result = {"ok": True, **retry_sre_agent("Knowledge install", install, attempts, retry_seconds,
                                            prefix="knowledge")}
    write_json(state_directory(values) / KNOWLEDGE_EVIDENCE, result)
    progress(f"[knowledge] Uploaded {len(documents)} files: {', '.join(documents)}.")
    if not result["indexed"]:
        progress("[knowledge] Indexing continues in the background; run status to check.")
    return result


def knowledge_status(values):
    documents = load_knowledge_documents()
    listing = knowledge_listing(sre_agent_url(values), sre_agent_token(values))
    return knowledge_report(listing, documents, installed=False)


def remove_knowledge(values, attempts=8, retry_seconds=15):
    documents = load_knowledge_documents()
    endpoint = sre_agent_url(values)
    token = sre_agent_token(values)

    def remove():
        for name in documents:
            try:
                sre_agent_request(
                    endpoint, token,
                    KNOWLEDGE_BASE + "document/" + urllib.parse.quote(name, safe=""),
                    "DELETE", lenient=True)
            except SreAgentRequestError as error:
                if error.status != 404:
                    raise
        report = knowledge_report(knowledge_listing(endpoint, token), documents, installed=False)
        if any(report["documents"].values()):
            raise SreAgentRequestError(
                "The knowledge files are still listed after deletion. If this persists, delete "
                "them from the agent's Knowledge settings page.",
                retryable=True,
            )
        return report

    retry_sre_agent("Knowledge removal", remove, attempts, retry_seconds, prefix="knowledge")
    (state_directory(values) / KNOWLEDGE_EVIDENCE).unlink(missing_ok=True)
    progress(f"[knowledge] Removed {len(documents)} files.")
    return {
        "ok": True,
        "removed": list(documents),
        "checkedUtc": datetime.now(timezone.utc).isoformat(),
    }


def knowledge(values, action):
    if action not in KNOWLEDGE_ACTIONS:
        raise DeploymentError("Knowledge action must be install, status, or remove.")
    return {
        "install": ensure_knowledge,
        "status": knowledge_status,
        "remove": remove_knowledge,
    }[action](values)


def sre_collection(document, label):
    items = document.get("value") if isinstance(document, dict) else document
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise DeploymentError(f"Azure SRE Agent returned an invalid {label} collection.")
    names = [item.get("name") for item in items]
    if (not all(isinstance(name, str) and name.strip() for name in names)
            or len(names) != len(set(names))):
        raise DeploymentError(f"Azure SRE Agent returned unnamed or duplicate {label} entries.")
    return items


def incident_state_path(values):
    name = required(values, "AZURE_ENV_NAME")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", name):
        raise DeploymentError("Invalid incident-workflow environment name.")
    return ROOT / ".workshop" / name / INCIDENT_WORKFLOW_STATE


@contextmanager
def incident_workflow_lock(values):
    path = incident_state_path(values).with_suffix(".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as lock:
            lock.write(f"{os.getpid()}\n")
    except FileExistsError as error:
        raise DeploymentError(
            "An incident-workflow operation is locked. Check its status and the recorded "
            "process before removing a stale incident-workflow-state.lock.") from error
    try:
        yield
    finally:
        path.unlink()


def save_incident_state(values, state):
    path = incident_state_path(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    write_json(temporary, state)
    temporary.replace(path)


def incident_agent_id(values):
    agent_id = required(values, "SRE_AGENT_RESOURCE_ID")
    match = re.fullmatch(
        r"(/subscriptions/[0-9a-fA-F-]+/resourceGroups/[\w.()-]+)/"
        r"providers/Microsoft\.App/agents/[\w-]+", agent_id)
    if not match or match.group(1).split("/")[2].lower() != required(
            values, "AZURE_SUBSCRIPTION_ID").lower():
        raise DeploymentError("Unexpected incident-workflow agent resource identifier.")
    try:
        uuid.UUID(match.group(1).split("/")[2])
    except ValueError as error:
        raise DeploymentError("The incident subscription ID must be a GUID.") from error
    if match.group(1).split("/")[4].lower() != required(values, "RESOURCE_GROUP").lower():
        raise DeploymentError("The incident workflow must use the workshop resource group.")
    return agent_id, match.group(1)


def load_incident_definition(template=None):
    template = Path(template or INCIDENT_WORKFLOW_TEMPLATE)
    try:
        parsed = yaml.safe_load(template.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise DeploymentError("The incident-workflow template is not valid YAML.") from error
    document = health_check_mapping(parsed, "The incident-workflow template")
    trigger = health_check_mapping(document.get("trigger"), "trigger")
    agent = health_check_mapping(document.get("custom_agent"), "custom_agent")
    filters = health_check_mapping(trigger.get("filters"), "trigger.filters")
    if (document.get("name") != INCIDENT_WORKFLOW_NAME
            or agent.get("name") != INCIDENT_WORKFLOW_AGENT):
        raise DeploymentError("Incident workflow and investigator names are fixed for safe ownership.")
    if trigger.get("type") != "incident-platform" or trigger.get("platform") != "azure-monitor":
        raise DeploymentError("The incident workflow requires the Azure Monitor incident platform.")
    if trigger.get("action_mode") != INCIDENT_MODE or agent.get("action_mode") != INCIDENT_MODE:
        raise DeploymentError("The incident workflow must run in Autonomous mode.")
    if (filters.get("severities") != ["Sev1", "Sev2"]
            or filters.get("alert_rules") != list(INCIDENT_ALERT_RULES)
            or filters.get("merge_window_hours") != 3):
        raise DeploymentError("Incident filters must retain Sev1/Sev2, the Orders rules, and a three-hour merge window.")
    tools = health_check_mapping(agent.get("tools"), "custom_agent.tools")
    if set(tools) != {"read_only", "autonomous_write", "ask_approval", "deny"}:
        raise DeploymentError(
            "Incident tools require read_only, autonomous_write, ask_approval, and deny groups.")
    read_only = health_check_tools(tools["read_only"], "Incident read-only tools")
    write = health_check_tools(tools["autonomous_write"], "Incident autonomous write tools")
    ask = health_check_tools(tools["ask_approval"], "Incident approval tools")
    denied = health_check_tools(tools["deny"], "Incident denied tools")
    if (set(read_only) != INCIDENT_READ_TOOLS or set(write) != INCIDENT_WRITE_TOOLS
            or set(ask) != INCIDENT_APPROVAL_TOOLS
            or not {"RunInTerminal", "Terminal"} <= set(denied)
            or (set(read_only) | set(write) | set(ask)) & set(denied)):
        raise DeploymentError(
            "The incident workflow may attach only approved read tools, the bounded Azure write tool, "
            "or separately approved follow-up tools.")
    entries = agent.get("skills")
    if not isinstance(entries, list) or len(entries) != len(INCIDENT_SKILLS):
        raise DeploymentError("The incident workflow requires exactly its three namespaced skills.")
    skills = []
    for entry, expected in zip(entries, INCIDENT_SKILLS):
        entry = health_check_mapping(entry, "An incident skill")
        if entry.get("name") != expected:
            raise DeploymentError("The incident workflow skill names or order differ.")
        source = health_check_text(entry.get("source"), "An incident skill source")
        skills.append(read_health_check_skill(template.parent / source, expected))
    sources = agent.get("context_sources")
    expected_sources = (
        AGENT_DEFINITIONS / "instructions" / "architecture.md",
        AGENT_DEFINITIONS / "instructions" / "investigation-principles.md",
        AGENT_DEFINITIONS / "runbooks" / "dependency-failure.md",
    )
    if not isinstance(sources, list) or len(sources) != len(expected_sources):
        raise DeploymentError("The RCA requires the canonical workshop context.")
    context = []
    for source, expected in zip(sources, expected_sources):
        if not isinstance(source, str) or (template.parent / source).resolve() != expected.resolve():
            raise DeploymentError("Incident context must use the canonical architecture and runbook files.")
        text = expected.read_text(encoding="utf-8")
        text = re.sub(r"\A---\s*\n.*?\n---\s*\n", "", text, count=1, flags=re.DOTALL)
        context.append(text.strip())
    skills[0]["content"] += "\n\n## Installed workshop reference context\n\n" + "\n\n".join(context) + "\n"
    return {
        "name": INCIDENT_WORKFLOW_NAME, "agent": INCIDENT_WORKFLOW_AGENT,
        "handoffDescription": health_check_text(agent.get("handoff_description"), "Incident handoff description"),
        "instructions": health_check_text(agent.get("instructions"), "Incident instructions"),
        "tools": read_only, "writeTools": write, "approvalTools": ask, "skills": skills,
    }


def incident_settings(source_repository=None, github_repository=None,
                      outlook_connector=None, email_to=()):
    def repository(value):
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(
                r"https://github\.com/[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", value):
            raise DeploymentError("An incident repository must be an exact https://github.com/owner/repo URL.")
        if value.rsplit("/", 1)[1] in {".", ".."} or value.endswith(".git"):
            raise DeploymentError("Use the repository page URL, without .git or a path suffix.")
        return value

    source = repository(source_repository)
    github = repository(github_repository)
    if not isinstance(email_to, (list, tuple)) or not all(
            isinstance(item, str) for item in email_to):
        raise DeploymentError("Email recipients must be a list of individual addresses.")
    recipients = list(dict.fromkeys(email_to))
    if bool(outlook_connector) != bool(recipients):
        raise DeploymentError("Email follow-up needs both --outlook-connector and --email-to.")
    if outlook_connector:
        health_check_name(outlook_connector, "Outlook connector")
    for recipient in recipients:
        if not isinstance(recipient, str) or not re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+",
                recipient):
            raise DeploymentError("Each --email-to must be a single email address, without display name, CC, or BCC.")
    return {
        "sourceRepository": source, "githubRepository": github,
        "outlookConnector": outlook_connector, "emailRecipients": recipients,
    }


def incident_bindings(values, settings):
    agent_id, group_id = incident_agent_id(values)
    bindings = {
        "agentResourceId": agent_id, "resourceGroupId": group_id,
        "subscriptionId": required(values, "AZURE_SUBSCRIPTION_ID"),
        "workspaceId": required(values, "LOG_ANALYTICS_CUSTOMER_ID"),
        "allowedAlertRules": list(INCIDENT_ALERT_RULES),
        **settings,
    }
    try:
        uuid.UUID(bindings["workspaceId"])
    except ValueError as error:
        raise DeploymentError("The incident workspace ID must be a GUID.") from error
    for key, provider in (
        ("VM_RESOURCE_ID", "Microsoft.Compute/virtualMachines"),
        ("LOG_ANALYTICS_ID", "Microsoft.OperationalInsights/workspaces"),
        ("POSTGRESQL_SERVER_RESOURCE_ID", "Microsoft.DBforPostgreSQL/flexibleServers"),
        ("POSTGRESQL_FAULT_RULE_RESOURCE_ID", "Microsoft.Network/networkSecurityGroups"),
    ):
        resource_id = required(values, key)
        prefix = group_id + "/providers/" + provider + "/"
        if not resource_id.lower().startswith(prefix.lower()) or any(
                char.isspace() for char in resource_id):
            raise DeploymentError(f"{key} must refer to a resource in the workshop group.")
        bindings[key] = resource_id
    if not bindings["POSTGRESQL_FAULT_RULE_RESOURCE_ID"].lower().endswith(
            "/securityrules/postgresqlfaultinjection"):
        raise DeploymentError("The incident workflow must use only PostgreSqlFaultInjection.")
    return bindings


def incident_prerequisites(values):
    agent_id, group_id = incident_agent_id(values)
    parent = az("rest", "--method", "get", "--url",
                f"https://management.azure.com{agent_id}?api-version={SRE_ARM_API_VERSION}")
    properties = parent.get("properties") if isinstance(parent, dict) else None
    action = properties.get("actionConfiguration") if isinstance(properties, dict) else None
    platform = properties.get("incidentManagementConfiguration") if isinstance(properties, dict) else None
    knowledge = properties.get("knowledgeGraphConfiguration") if isinstance(properties, dict) else None
    if (not isinstance(properties, dict) or str(parent.get("id", "")).lower() != agent_id.lower()
            or not isinstance(action, dict) or action.get("accessLevel") != "Low"
            or action.get("mode") != "Review"
            or not isinstance(platform, dict) or platform.get("type") != "AzMonitor"):
        raise DeploymentError("Incident workflow requires this exact Low-access, Review-mode Azure Monitor agent.")
    managed = knowledge.get("managedResources") if isinstance(knowledge, dict) else None
    if (not isinstance(managed, list) or not all(isinstance(item, str) for item in managed)
            or {item.lower() for item in managed} != {group_id.lower()}):
        raise DeploymentError(
            "The title partition requires an agent managing only this workshop resource group.")
    if str(properties.get("agentEndpoint", "")).rstrip("/") != sre_agent_url(values):
        raise DeploymentError("The ARM agent endpoint differs from the selected workshop endpoint.")
    return health_check_telemetry_tools(values)


def incident_alert_titles(values):
    _, group_id = incident_agent_id(values)
    titles = []
    for name in INCIDENT_ALERT_RULES:
        alert_id = group_id + "/providers/Microsoft.Insights/scheduledQueryRules/" + name
        alert = az("rest", "--method", "get", "--url",
                   f"https://management.azure.com{alert_id}?api-version=2023-12-01")
        properties = alert.get("properties") if isinstance(alert, dict) else None
        scopes = properties.get("scopes") if isinstance(properties, dict) else None
        if (not isinstance(properties, dict) or str(alert.get("id", "")).lower() != alert_id.lower()
                or alert.get("name") != name or properties.get("enabled") is not True
                or type(properties.get("severity")) is not int or properties.get("severity") != 1
                or not isinstance(scopes, list) or len(scopes) != 1
                or not isinstance(scopes[0], str)
                or scopes[0].lower() != required(values, "LOG_ANALYTICS_ID").lower()):
            raise DeploymentError(f"Workshop alert {name} is missing, disabled, or changed.")
        display = health_check_text(properties.get("displayName"), f"The display name of {name}")
        if len(display) > 200 or any(char in display for char in "\r\n"):
            raise DeploymentError("An alert display name cannot be used safely as a title filter.")
        titles.extend([name, display])
    titles = list(dict.fromkeys(titles))
    cpu_id = group_id + "/providers/Microsoft.Insights/metricAlerts/alert-orders-high-cpu"
    cpu = az("rest", "--method", "get", "--url",
             f"https://management.azure.com{cpu_id}?api-version=2018-03-01")
    properties = cpu.get("properties") if isinstance(cpu, dict) else None
    scopes = properties.get("scopes") if isinstance(properties, dict) else None
    if (not isinstance(properties, dict) or str(cpu.get("id", "")).lower() != cpu_id.lower()
            or cpu.get("name") != "alert-orders-high-cpu"
            or properties.get("enabled") is not True
            or type(properties.get("severity")) is not int or properties.get("severity") != 2
            or not isinstance(scopes, list) or len(scopes) != 1
            or not isinstance(scopes[0], str)
            or scopes[0].lower() != required(values, "VM_RESOURCE_ID").lower()):
        raise DeploymentError("The CPU alert is missing, disabled, or changed.")
    cpu_titles = ["alert-orders-high-cpu", health_check_text(
        properties.get("description"), "The CPU alert description")]
    if any(title.casefold() in candidate.casefold() for title in titles for candidate in cpu_titles):
        raise DeploymentError("An incident title filter would also include the CPU alert.")
    return titles


def incident_optional_get(endpoint, token, path, *, with_etag=False):
    try:
        return sre_agent_request(endpoint, token, path, with_etag=with_etag)
    except SreAgentRequestError as error:
        if error.status == 404:
            return (None, None) if with_etag else None
        raise


def incident_capabilities(endpoint, token, settings):
    capabilities = {
        "source": {"enabled": False, "reason": "No source repository was requested."},
        "github": {"enabled": False, "reason": "No incident repository was requested; draft only."},
        "email": {"enabled": False, "reason": "No Outlook connection and recipients were requested; draft only."},
    }
    repositories = []
    oauth = False
    if settings["sourceRepository"] or settings["githubRepository"]:
        document = incident_optional_get(endpoint, token, "/api/v2/repos")
        if document is not None:
            repositories = sre_collection(document, "repository")
        domains = incident_optional_get(endpoint, token, "/api/v2/github/domains")
        oauth = isinstance(domains, dict) and isinstance(domains.get("values"), list) and bool(domains["values"])

    def connected(url):
        return oauth and any(
            isinstance(item.get("properties"), dict)
            and str(item["properties"].get("url", "")).rstrip("/").lower() == url.lower()
            for item in repositories)

    source = settings["sourceRepository"]
    if source:
        capabilities["source"] = {
            "enabled": connected(source),
            "reason": "Bound Code Access repository and GitHub authentication found."
            if connected(source) else "The exact source repository or GitHub authentication is unavailable.",
        }
    policy = None
    if settings["githubRepository"] or settings["outlookConnector"]:
        policy = incident_optional_get(endpoint, token, "/api/v2/agent/settings/global")
    permissions = policy.get("permissions") if isinstance(policy, dict) else None

    def approval(tool):
        if not isinstance(permissions, dict):
            return False
        groups = [permissions.get(key) for key in ("allow", "ask", "deny")]
        if not all(isinstance(group, list) and all(isinstance(item, str) for item in group)
                   for group in groups):
            return False
        allow, ask, deny = groups
        return (tool in ask
                and not any(fnmatch.fnmatchcase(tool, item) for item in [*allow, *deny]))

    github = settings["githubRepository"]
    if github:
        enabled = connected(github) and approval("CreateGithubIssue")
        capabilities["github"] = {
            "enabled": enabled,
            "reason": "Bound repository, GitHub authentication, and explicit ask policy verified."
            if enabled else "Connect the exact incident repository and require CreateGithubIssue approval "
            "without a matching allow/deny override; draft only.",
        }
    connector = settings["outlookConnector"]
    if connector:
        server = incident_optional_get(endpoint, token, f"/api/v2/connectorV2/mcpservers/{connector}")
        properties = server.get("properties") if isinstance(server, dict) else None
        connectors = properties.get("connectors") if isinstance(properties, dict) else None
        binding = connectors[0] if (
            isinstance(connectors, list) and len(connectors) == 1
            and isinstance(connectors[0], dict) and connectors[0].get("name") == "office365"
            and isinstance(connectors[0].get("connectionName"), str)) else None
        authenticated = False
        if binding:
            connection_name = health_check_name(binding["connectionName"], "Outlook connection name")
            connection = incident_optional_get(
                endpoint, token, f"/api/v2/connectorV2/connections/{connection_name}")
            authenticated = (isinstance(connection, dict)
                             and isinstance(connection.get("properties"), dict)
                             and connection["properties"].get("overallStatus") == "Connected")
        enabled = authenticated and approval("SendOutlookEmail")
        capabilities["email"] = {
            "enabled": enabled,
            "reason": "Bound Office 365 connection, authenticated state, recipients, and ask policy verified."
            if enabled else "The bound Office 365 connection must be Connected and SendOutlookEmail "
            "must require approval without a matching allow/deny override; draft only.",
        }
    return capabilities


def incident_documents(definition, values, settings, capabilities, telemetry_tools, titles):
    effective = copy.deepcopy(definition)
    permitted = set(HEALTH_CHECK_TOOLS) | INCIDENT_WRITE_TOOLS
    for capability, tools in (
        ("source", INCIDENT_SOURCE_TOOLS), ("github", INCIDENT_GITHUB_TOOLS),
        ("email", INCIDENT_EMAIL_TOOLS),
    ):
        if capabilities[capability]["enabled"]:
            permitted.update(tools)
    effective["tools"] = [
        tool for tool in [*definition["tools"], *definition["writeTools"], *definition["approvalTools"]]
        if tool in permitted
    ]
    bindings = incident_bindings(values, settings)
    bindings["capabilities"] = capabilities
    effective["instructions"] += (
        "\n\nTrusted installation bindings (not credentials):\n"
        + json.dumps(bindings, indent=2, sort_keys=True))
    skills, agent = skill_agent_documents(effective, telemetry_tools)
    documents = {
        INCIDENT_BASE + "skills/" + skill["name"]: skill for skill in skills
    }
    documents[INCIDENT_BASE + "agents/" + agent["name"]] = agent
    documents[INCIDENT_BASE + "incidentFilters/" + INCIDENT_WORKFLOW_NAME] = {
        "name": INCIDENT_WORKFLOW_NAME, "type": "IncidentFilter", "tags": [],
        "properties": {
            "incidentPlatform": "AzMonitor", "priorities": ["Sev1", "Sev2"],
            "handlingAgent": INCIDENT_WORKFLOW_AGENT, "agentMode": INCIDENT_MODE,
            "impactedService": "", "incidentType": "", "incidentTypes": [], "alertId": "",
            "handlingAgents": None, "owningTeamId": "", "owningTeamIds": [],
            "titleContains": INCIDENT_WORKFLOW_TITLE, "titleContainsAll": [],
            "titleContainsAny": [], "titleNotContains": [],
            "icmFilterSettings": None,
            "azMonitorFilterSettings": {"targetResourceType": "", "targetResource": ""},
            "mergeEnabled": True, "mergeWindowHours": 3,
            "maxAutomatedInvestigationAttempts": 3, "isEnabled": True,
        },
    }
    return documents


def incident_component_matches(actual, expected):
    if not isinstance(actual, dict) or actual.get("name") != expected["name"]:
        return False
    properties = actual.get("properties")
    if not isinstance(properties, dict):
        return False
    expected_properties = expected["properties"]
    if expected.get("type") == "IncidentFilter":
        properties = {key: value for key, value in properties.items()
                      if key not in INCIDENT_PLAN_READ_ONLY_PROPERTIES}
        expected_properties = {key: value for key, value in expected_properties.items()
                               if key not in INCIDENT_PLAN_READ_ONLY_PROPERTIES}
    if expected["name"] == SRE_RESPONSE_PLAN_NAME and set(properties) != set(expected_properties):
        return False
    for key, value in expected_properties.items():
        current = properties.get(key)
        if key == "name" and expected.get("type") == "Skill" and key not in properties:
            current = actual["name"]
        if key == "skillContent":
            current = properties.get("skillContent", properties.get("skillMdContent"))
            if not isinstance(current, str) or not health_check_skill_matches(actual, {"content": value}):
                return False
        elif isinstance(value, str):
            if key in {"instructions", "handoffDescription", "description"}:
                matches = health_check_text_matches(current, value)
            elif key == "agentMode" and isinstance(current, str):
                matches = current.casefold() == value.casefold()
            else:
                matches = current == value
            if not isinstance(current, str) or not matches:
                return False
        elif isinstance(value, list):
            if not isinstance(current, list):
                return False
            if all(isinstance(item, str) for item in value):
                if not all(isinstance(item, str) for item in current) or sorted(current) != sorted(value):
                    return False
            elif current != value:
                return False
        elif isinstance(value, bool):
            if current is not value:
                return False
        elif current != value or isinstance(current, bool):
            return False
    actual_tags = actual.get("tags")
    expected_tags = expected.get("tags")
    return ([] if actual_tags is None else actual_tags) == (
        [] if expected_tags is None else expected_tags)


def incident_plan_document(plan):
    if (not isinstance(plan, dict) or plan.get("name") != SRE_RESPONSE_PLAN_NAME
            or not isinstance(plan.get("properties"), dict)):
        raise DeploymentError("The original workshop response plan is missing or malformed.")
    properties = plan["properties"]
    priorities = properties.get("priorities")
    if (properties.get("incidentPlatform") != "AzMonitor"
            or properties.get("agentMode") != "Review"
            or properties.get("handlingAgent") != "meta_agent"
            or properties.get("isEnabled") is not True
            or not isinstance(priorities, list)
            or not all(isinstance(item, str) for item in priorities)
            or set(priorities) != {"Sev1", "Sev2"}):
        raise DeploymentError("The original response plan must be enabled for Sev1/Sev2 in Review mode with meta_agent.")
    for key in ("titleContainsAll", "titleContainsAny", "titleNotContains"):
        value = properties.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
            raise DeploymentError(f"The original response plan has an invalid {key} filter.")
    if not isinstance(properties.get("titleContains", ""), str):
        raise DeploymentError("The original response plan has an invalid titleContains filter.")
    return {
        "name": SRE_RESPONSE_PLAN_NAME, "type": "IncidentFilter",
        "tags": copy.deepcopy([] if plan.get("tags") is None else plan["tags"]),
        "properties": {key: copy.deepcopy(value) for key, value in properties.items()
                       if key not in INCIDENT_PLAN_READ_ONLY_PROPERTIES},
    }


def incident_title_matches(properties, title):
    title = title.casefold()
    contains = properties.get("titleContains", "")
    all_terms = properties.get("titleContainsAll", [])
    any_terms = properties.get("titleContainsAny", [])
    excluded = properties.get("titleNotContains", [])
    if (not isinstance(contains, str)
            or not all(isinstance(group, list) and all(isinstance(item, str) for item in group)
                       for group in (all_terms, any_terms, excluded))):
        raise DeploymentError("A response plan has unsupported title-filter values.")
    return (contains.casefold() in title
            and all(term.casefold() in title for term in all_terms)
            and (not any_terms or any(term.casefold() in title for term in any_terms))
            and not any(term.casefold() in title for term in excluded))


def incident_read_state(values):
    path = incident_state_path(values)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding="utf-8"))
    agent_id, _ = incident_agent_id(values)
    paths = {
        *(INCIDENT_BASE + "skills/" + name for name in INCIDENT_SKILLS),
        INCIDENT_BASE + "agents/" + INCIDENT_WORKFLOW_AGENT,
        INCIDENT_BASE + "incidentFilters/" + INCIDENT_WORKFLOW_NAME,
    }
    if (not isinstance(state, dict) or state.get("version") != 1
            or state.get("agentResourceId") != agent_id
            or state.get("endpoint") != sre_agent_url(values)
            or state.get("phase") not in {"prepared", "active", "partial", "removing"}
            or not isinstance(state.get("documents"), dict)
            or set(state["documents"]) != paths
            or not isinstance(state.get("capabilities"), dict)
            or set(state["capabilities"]) != {"source", "github", "email"}
            or not all(isinstance(item, dict) and isinstance(item.get("enabled"), bool)
                       and isinstance(item.get("reason"), str)
                       for item in state["capabilities"].values())
            or not isinstance(state.get("titles"), list)
            or not state["titles"] or not all(isinstance(item, str) and item.strip() for item in state["titles"])):
        raise DeploymentError("Incident rollback state is malformed or belongs to another agent; do not overwrite it.")
    original = incident_plan_document(state.get("originalPlan"))
    expected = copy.deepcopy(original)
    expected["properties"]["titleContains"] = (
        original["properties"].get("titleContains") or INCIDENT_DEFAULT_TITLE)
    partitioned = incident_plan_document(state.get("partitionedPlan"))
    if partitioned != expected:
        raise DeploymentError("The saved incident filter partition differs from the original snapshot.")
    state["originalPlan"] = original
    state["partitionedPlan"] = partitioned
    settings = state.get("settings")
    if not isinstance(settings, dict) or settings != incident_settings(
            settings.get("sourceRepository"), settings.get("githubRepository"),
            settings.get("outlookConnector"), settings.get("emailRecipients", [])):
        raise DeploymentError("Incident follow-up bindings in rollback state are invalid.")
    for path, document in state["documents"].items():
        if (not isinstance(document, dict) or document.get("name") != path.rsplit("/", 1)[1]
                or not isinstance(document.get("properties"), dict)):
            raise DeploymentError("A saved incident component is invalid.")
    tools = state["documents"][INCIDENT_BASE + "agents/" + INCIDENT_WORKFLOW_AGENT]["properties"].get("tools")
    if (not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools)
            or set(tools) - (INCIDENT_READ_TOOLS | INCIDENT_WRITE_TOOLS | INCIDENT_APPROVAL_TOOLS
                             | set(HEALTH_CONNECTOR_TOOLS.values()))):
        raise DeploymentError("Rollback state contains an unapproved incident tool.")
    return state


def incident_routing_check(plans, partitioned, module):
    documents = {item.get("name"): item for item in plans}
    documents[SRE_RESPONSE_PLAN_NAME] = partitioned
    documents[INCIDENT_WORKFLOW_NAME] = module
    for title in INCIDENT_ALERT_RULES:
        matches = []
        for name, item in documents.items():
            properties = item.get("properties")
            if not isinstance(properties, dict):
                raise DeploymentError("A response plan has invalid properties.")
            priorities = properties.get("priorities", [])
            if not isinstance(priorities, list) or not all(isinstance(item, str) for item in priorities):
                raise DeploymentError("A response plan has invalid priorities.")
            if (properties.get("isEnabled") is True
                    and properties.get("incidentPlatform") == "AzMonitor"
                    and set(priorities) & {"Sev1", "Sev2"}
                    and incident_title_matches(properties, title)):
                matches.append(name)
        if matches != [INCIDENT_WORKFLOW_NAME]:
            raise DeploymentError("The incident title matches another enabled response plan; resolve routing overlap first.")
    if not incident_title_matches(partitioned["properties"], "alert-orders-high-cpu"):
        raise DeploymentError("The existing response plan would no longer route the CPU alert.")
    if not incident_title_matches(partitioned["properties"], "alert-orders-http-5xx"):
        raise DeploymentError("The existing response plan would no longer route the HTTP 5xx alert.")


def incident_guarded_put(endpoint, token, path, desired, allowed, attempts, retry_seconds):
    current, etag = incident_optional_get(endpoint, token, path, with_etag=True)
    if incident_component_matches(current, desired):
        return
    if not any(current is None if item is None else incident_component_matches(current, item)
               for item in allowed):
        raise DeploymentError(f"Concurrent or unowned configuration at {path}; refusing to overwrite it.")
    sre_agent_request(endpoint, token, path, "PUT", desired, if_match=etag)

    def verify():
        actual = incident_optional_get(endpoint, token, path)
        if not incident_component_matches(actual, desired):
            raise SreAgentRequestError(
                f"Incident component {desired['name']} did not match its read-back.", retryable=True)
    retry_sre_agent("Incident read-back", verify, attempts, retry_seconds, prefix="incident-workflow")


def incident_workflow_status(values):
    endpoint, token = sre_agent_url(values), sre_agent_token(values)
    state = incident_read_state(values)
    plans = sre_collection(sre_agent_request(endpoint, token, INCIDENT_BASE + "incidentFilters"), "response-plan")
    if state is None:
        present = []
        for path in [
            *(INCIDENT_BASE + "skills/" + name for name in INCIDENT_SKILLS),
            INCIDENT_BASE + "agents/" + INCIDENT_WORKFLOW_AGENT,
            INCIDENT_BASE + "incidentFilters/" + INCIDENT_WORKFLOW_NAME,
        ]:
            if incident_optional_get(endpoint, token, path) is not None:
                present.append(path.rsplit("/", 1)[1])
        return {
            "installed": False, "matchesDefinition": False, "phase": "absent",
            "problems": ["Module components exist without local rollback state; do not overwrite them."]
            if present else ["The optional incident workflow is not installed."],
            "unownedComponents": present, "checkedUtc": datetime.now(timezone.utc).isoformat(),
        }
    problems = []
    components = {}
    for path, expected in state["documents"].items():
        actual = incident_optional_get(endpoint, token, path)
        components[expected["name"]] = actual is not None
        if not incident_component_matches(actual, expected):
            problems.append(f"Component {expected['name']} is missing or differs from the installed definition.")
    baseline = next((item for item in plans if item.get("name") == SRE_RESPONSE_PLAN_NAME), None)
    if not incident_component_matches(baseline, state["partitionedPlan"]):
        problems.append("The original plan no longer has the saved complementary title filter.")
    if state["phase"] != "active":
        problems.append(f"An incident operation is incomplete (phase {state['phase']}); run remove to restore routing.")
    try:
        telemetry_tools = incident_prerequisites(values)
        if incident_alert_titles(values) != state["titles"]:
            problems.append("The deployed alert names or display names changed; remove and reinstall.")
        capabilities = incident_capabilities(endpoint, token, state["settings"])
        desired = incident_documents(
            load_incident_definition(), values, state["settings"], capabilities, telemetry_tools, state["titles"])
        if desired != state["documents"]:
            problems.append("The repository definition or optional capabilities changed; remove and reinstall.")
        incident_routing_check(
            plans, state["partitionedPlan"],
            state["documents"][INCIDENT_BASE + "incidentFilters/" + INCIDENT_WORKFLOW_NAME])
    except DeploymentError as error:
        problems.append(str(error))
        capabilities = {
            name: {"enabled": False, "reason": "Current capability verification failed; see problems."}
            for name in ("source", "github", "email")
        }
    return {
        "installed": all(components.values()) and baseline is not None,
        "matchesDefinition": not problems, "phase": state["phase"],
        "responsePlan": INCIDENT_WORKFLOW_NAME, "handlingAgent": INCIDENT_WORKFLOW_AGENT,
        "routing": {"includedTitles": state["titles"],
                    "workflowTitleContains": INCIDENT_WORKFLOW_TITLE,
                    "originalTitleContains": state["partitionedPlan"]["properties"]["titleContains"],
                    "priorities": ["Sev1", "Sev2"], "mode": INCIDENT_MODE},
        "components": components, "followups": capabilities,
        "problems": problems, "checkedUtc": datetime.now(timezone.utc).isoformat(),
    }


def remove_incident_components(values, endpoint, token, state, attempts, retry_seconds):
    module_path = INCIDENT_BASE + "incidentFilters/" + INCIDENT_WORKFLOW_NAME
    active = state["documents"][module_path]
    disabled = copy.deepcopy(active)
    disabled["properties"]["isEnabled"] = False
    current = incident_optional_get(endpoint, token, module_path)
    if current is not None and not (
            isinstance(current.get("properties"), dict)
            and current["properties"].get("isEnabled") is False):
        incident_guarded_put(endpoint, token, module_path, disabled, [active, disabled],
                             attempts, retry_seconds)
    baseline_path = INCIDENT_BASE + "incidentFilters/" + SRE_RESPONSE_PLAN_NAME
    partial_anchor = copy.deepcopy(state["originalPlan"])
    partial_anchor["properties"]["titleContains"] = state["partitionedPlan"]["properties"]["titleContains"]
    partial_exclusion = copy.deepcopy(state["originalPlan"])
    partial_exclusion["properties"]["titleNotContains"] = state["partitionedPlan"]["properties"]["titleNotContains"]
    incident_guarded_put(endpoint, token, baseline_path, state["originalPlan"],
                         [state["partitionedPlan"], state["originalPlan"],
                          partial_anchor, partial_exclusion], attempts, retry_seconds)
    targets = [module_path, INCIDENT_BASE + "agents/" + INCIDENT_WORKFLOW_AGENT,
               *(INCIDENT_BASE + "skills/" + name for name in INCIDENT_SKILLS)]
    for path in targets:
        current, etag = incident_optional_get(endpoint, token, path, with_etag=True)
        if current is None:
            continue
        expected = disabled if path == module_path else state["documents"][path]
        if not incident_component_matches(current, expected):
            raise DeploymentError(f"Component {expected['name']} changed; refusing to delete it.")
        try:
            sre_agent_request(endpoint, token, path, "DELETE", if_match=etag)
        except SreAgentRequestError as error:
            if error.status != 404:
                raise

        def verify_deleted():
            if incident_optional_get(endpoint, token, path) is not None:
                raise SreAgentRequestError(f"Component {expected['name']} is still present.", retryable=True)
        retry_sre_agent("Incident removal", verify_deleted, attempts, retry_seconds, prefix="incident-workflow")
    incident_state_path(values).unlink()
    (incident_state_path(values).parent / INCIDENT_WORKFLOW_EVIDENCE).unlink(missing_ok=True)
    return {"ok": True, "removed": [path.rsplit("/", 1)[1] for path in targets],
            "restoredResponsePlan": SRE_RESPONSE_PLAN_NAME,
            "checkedUtc": datetime.now(timezone.utc).isoformat()}


def ensure_incident_workflow(values, settings=None, attempts=8, retry_seconds=15):
    definition = load_incident_definition()
    endpoint, token = sre_agent_url(values), sre_agent_token(values)
    with incident_workflow_lock(values):
        saved = incident_read_state(values)
        if saved is not None:
            if settings is not None and settings != saved["settings"]:
                raise DeploymentError("Remove the incident workflow before changing its trusted destinations.")
            report = incident_workflow_status(values)
            if not report["matchesDefinition"]:
                raise DeploymentError("The incident workflow changed or is partial; run remove first. "
                                      + " ".join(report["problems"]))
            return {"ok": True, **report}
        settings = settings or incident_settings()
        telemetry_tools = incident_prerequisites(values)
        titles = incident_alert_titles(values)
        capabilities = incident_capabilities(endpoint, token, settings)
        documents = incident_documents(definition, values, settings, capabilities, telemetry_tools, titles)
        plans = sre_collection(sre_agent_request(
            endpoint, token, INCIDENT_BASE + "incidentFilters"), "response-plan")
        original = incident_plan_document(
            next((item for item in plans if item.get("name") == SRE_RESPONSE_PLAN_NAME), None))
        partitioned = copy.deepcopy(original)
        partitioned["properties"]["titleContains"] = (
            original["properties"].get("titleContains") or INCIDENT_DEFAULT_TITLE)
        module_path = INCIDENT_BASE + "incidentFilters/" + INCIDENT_WORKFLOW_NAME
        incident_routing_check(plans, partitioned, documents[module_path])
        for path in documents:
            if incident_optional_get(endpoint, token, path) is not None:
                raise DeploymentError("An incident component already exists without rollback state; refusing to replace it.")
        state = {
            "version": 1, "agentResourceId": required(values, "SRE_AGENT_RESOURCE_ID"),
            "endpoint": endpoint, "phase": "prepared", "settings": settings,
            "titles": titles, "capabilities": capabilities, "documents": documents,
            "originalPlan": original, "partitionedPlan": partitioned,
        }
        save_incident_state(values, state)
        try:
            for path, desired in documents.items():
                staged = copy.deepcopy(desired)
                if path == module_path:
                    staged["properties"]["isEnabled"] = False
                incident_guarded_put(endpoint, token, path, staged, [None], attempts, retry_seconds)
            incident_guarded_put(
                endpoint, token, INCIDENT_BASE + "incidentFilters/" + SRE_RESPONSE_PLAN_NAME,
                partitioned, [original], attempts, retry_seconds)
            disabled = copy.deepcopy(documents[module_path])
            disabled["properties"]["isEnabled"] = False
            incident_guarded_put(endpoint, token, module_path, documents[module_path], [disabled],
                                 attempts, retry_seconds)
            state["phase"] = "active"
            save_incident_state(values, state)
            report = incident_workflow_status(values)
            if not report["matchesDefinition"]:
                raise DeploymentError("Incident workflow activation failed: " + " ".join(report["problems"]))
            result = {"ok": True, **report}
            write_json(incident_state_path(values).parent / INCIDENT_WORKFLOW_EVIDENCE, result)
        except (DeploymentError, OSError) as error:
            state["phase"] = "partial"
            save_incident_state(values, state)
            try:
                remove_incident_components(values, endpoint, token, state, attempts, retry_seconds)
            except (DeploymentError, OSError) as rollback_error:
                raise DeploymentError(
                    f"{error} Rollback is incomplete: {rollback_error}. Run incident-workflow "
                    "status and reconcile before retrying; do not enable an overlapping plan.") from error
            raise
        progress("[incident-workflow] Installed the autonomous PostgreSQL plan and the "
                 "complementary Sev1/Sev2 Review plan.")
        for name, capability in capabilities.items():
            if not capability["enabled"]:
                progress(f"[incident-workflow] {name}: {capability['reason']}")
        return result


def remove_incident_workflow(values, attempts=8, retry_seconds=15):
    endpoint, token = sre_agent_url(values), sre_agent_token(values)
    with incident_workflow_lock(values):
        state = incident_read_state(values)
        if state is None:
            report = incident_workflow_status(values)
            if report["unownedComponents"]:
                raise DeploymentError("Rollback state is missing; do not guess the original plan or delete unowned components.")
            return {"ok": True, "removed": [], "checkedUtc": datetime.now(timezone.utc).isoformat()}
        state["phase"] = "removing"
        save_incident_state(values, state)
        return remove_incident_components(values, endpoint, token, state, attempts, retry_seconds)


def incident_workflow(values, action, settings=None):
    if action not in INCIDENT_WORKFLOW_ACTIONS:
        raise DeploymentError("Incident-workflow action must be install, status, or remove.")
    if settings is not None and action != "install":
        raise DeploymentError("Incident destination options are valid only with install.")
    if action == "install":
        return ensure_incident_workflow(values, settings)
    if action == "remove":
        return remove_incident_workflow(values)
    return incident_workflow_status(values)


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
    status, database = request_json(url, "/database")
    if (status != 200 or not isinstance(database, dict)
            or database.get("provider") != "PostgreSQL"
            or database.get("status") != "ready"
            or not str(database.get("serverVersion", "")).startswith("16.")
            or database.get("schemaVersion") != 1
            or not isinstance(database.get("databaseBytes"), int)
            or database["databaseBytes"] <= 0):
        raise DeploymentError(
            "Public /database must report a ready PostgreSQL schema.")
    for path in ("/fault/cpu", "/fault/postgresql", "/fault/reset"):
        try:
            request_json(url, path, "POST", {})
        except HttpError as error:
            if error.status == 404:
                continue
            raise
        raise DeploymentError(f"Destructive HTTP fault route unexpectedly exists: {path}")
    result = {"ok": True, "url": url, "ordersValidated": len(orders),
              "database": {"provider": database["provider"],
                           "schemaVersion": database["schemaVersion"]},
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
    for key in (
        "VM_RESOURCE_ID",
        "POSTGRESQL_SERVER_RESOURCE_ID",
        "LOG_ANALYTICS_ID",
        "SRE_AGENT_RESOURCE_ID",
    ):
        progress(f"{key}: {required(values, key)}")


def predown():
    remove_monitoring_contributor(environment())


def inspect_vm(values):
    result = run_vm(values, "/usr/bin/python3 /opt/orders-api/inspect.py '__WORKSHOP_REQUEST_ID__'")
    write_json(state_directory(values) / "vm-inspection.json", result)
    return result


def postgresql_fault_status(values):
    rule = az(
        "network", "nsg", "rule", "show",
        "--resource-group", required(values, "RESOURCE_GROUP"),
        "--nsg-name", required(values, "NETWORK_SECURITY_GROUP_NAME"),
        "--name", required(values, "POSTGRESQL_FAULT_RULE_NAME"),
        "--subscription", required(values, "AZURE_SUBSCRIPTION_ID"),
    )
    expected = {
        "priority": 100,
        "direction": "Outbound",
        "protocol": "Tcp",
        "sourceAddressPrefix": "10.240.0.0/27",
        "sourcePortRange": "*",
        "destinationAddressPrefix": "10.240.0.32/27",
        "destinationPortRange": "5432",
    }
    if not isinstance(rule, dict):
        raise DeploymentError("Azure did not return the PostgreSQL fault rule.")
    for key, value in expected.items():
        if rule.get(key) != value:
            raise DeploymentError(
                f"PostgreSQL fault rule {key} is not the expected bounded value.")
    access = rule.get("access")
    if access not in {"Allow", "Deny"}:
        raise DeploymentError("PostgreSQL fault rule has an unexpected access state.")
    return {
        "postgresql": "active" if access == "Deny" else "inactive",
        "postgresqlAccess": access,
    }


def deploy_postgresql_fault(values, inject):
    progress(
        f"[fault-postgresql] {'Injecting' if inject else 'Resetting'} "
        "the bounded NSG TCP 5432 rule")
    az(
        "deployment", "group", "create",
        "--name", "orders-postgresql-fault",
        "--resource-group", required(values, "RESOURCE_GROUP"),
        "--subscription", required(values, "AZURE_SUBSCRIPTION_ID"),
        "--template-file", str(ROOT / "infra" / "fault.bicep"),
        "--parameters",
        "networkSecurityGroupName="
        + required(values, "NETWORK_SECURITY_GROUP_NAME"),
        "injectPostgresqlFault=" + ("true" if inject else "false"),
        timeout=300,
    )
    result = postgresql_fault_status(values)
    expected = "active" if inject else "inactive"
    if result["postgresql"] != expected:
        raise DeploymentError(
            f"PostgreSQL fault rule did not reach the {expected} state.")
    return result


def vm_fault(values, action, numbers=()):
    command = "/usr/bin/python3 /opt/orders-api/faults.py " + " ".join(
        [action, *map(str, numbers), "--request-id", "__WORKSHOP_REQUEST_ID__"]
    )
    progress(f"[fault-{action}] Invoking authenticated Azure VM Run Command")
    return run_vm(values, command, timeout=240)


def fault(values, action, arguments):
    if action not in {
        "cpu",
        "postgresql",
        "status",
        "reset",
        "reset-cpu",
        "reset-postgresql",
    }:
        raise DeploymentError(
            "Fault action must be cpu, postgresql, status, reset-cpu, "
            "reset-postgresql, or reset.")
    if action == "cpu":
        defaults = (300, 2)
        limits = ((10, 1800), (1, 8))
        if len(arguments) > len(defaults):
            raise DeploymentError("Too many arguments for cpu.")
        try:
            numbers = [
                int(value) for value in arguments
            ] + list(defaults[len(arguments):])
        except (ValueError, TypeError) as error:
            raise DeploymentError("CPU fault arguments must be integers.") from error
        for number, (minimum, maximum) in zip(numbers, limits):
            if not minimum <= number <= maximum:
                raise DeploymentError(
                    f"cpu argument must be between {minimum} and {maximum}.")
        result = {
            **vm_fault(values, "cpu", numbers),
            **postgresql_fault_status(values),
        }
    elif arguments:
        raise DeploymentError(f"{action} does not accept additional arguments.")
    elif action == "postgresql":
        vm_fault(values, "status")
        postgresql = deploy_postgresql_fault(values, inject=True)
        try:
            application = vm_fault(values, "postgresql-denied")
        except DeploymentError as error:
            partial = {
                **postgresql,
                "ok": False,
                "verification": "failed",
                "error": str(error),
            }
            write_json(
                state_directory(values) / "fault-postgresql-partial.json",
                partial,
            )
            raise DeploymentError(
                "The NSG rule is verified as Deny, but the Orders API recycle "
                "did not prove controlled PostgreSQL failure. The operation is "
                "partial; run `python scripts/workshop.py fault status`, then "
                "retry this action or run `fault reset-postgresql`."
            ) from error
        result = {**postgresql, **application}
    elif action in {"reset-postgresql", "reset"}:
        postgresql = deploy_postgresql_fault(values, inject=False)
        try:
            application = vm_fault(values, "postgresql-ready")
        except DeploymentError as error:
            partial_name = (
                "reset-postgresql" if action == "reset-postgresql" else "reset"
            )
            partial = {
                **postgresql,
                "ok": False,
                "verification": "failed",
                "error": str(error),
            }
            write_json(
                state_directory(values) / f"fault-{partial_name}-partial.json",
                partial,
            )
            raise DeploymentError(
                "The NSG rule is verified as Allow, but the Orders API recycle "
                "did not prove PostgreSQL recovery. The operation is partial; "
                "run `python scripts/workshop.py fault status`, then retry "
                f"`fault {partial_name}`."
            ) from error
        result = {**postgresql, **application}
        if action == "reset":
            try:
                result = {**result, **vm_fault(values, "reset")}
            except DeploymentError as error:
                partial = {
                    **result,
                    "ok": False,
                    "verification": "failed",
                    "error": str(error),
                }
                write_json(
                    state_directory(values) / "fault-reset-partial.json",
                    partial,
                )
                raise DeploymentError(
                    "PostgreSQL recovery is verified, but the all-scenarios "
                    "reset did not verify CPU cleanup. Run `fault status`, then "
                    "retry `fault reset` or use `fault reset-cpu`."
                ) from error
    elif action == "reset-cpu":
        result = {
            **vm_fault(values, "reset"),
            **postgresql_fault_status(values),
        }
    else:
        result = {
            **postgresql_fault_status(values),
            **vm_fault(values, "postgresql-status"),
        }
    result["ok"] = True
    if action in {"postgresql", "reset-postgresql", "reset"}:
        partial_path = state_directory(values) / f"fault-{action}-partial.json"
        partial_path.unlink(missing_ok=True)
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
            "--resource", "https://api.loganalytics.io",
            "--subscription", required(values, "AZURE_SUBSCRIPTION_ID"),
            "--body", "@" + str(path),
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
    (AppRequests | where AppRoleName == 'orders-api' | summarize Samples=count() | extend Signal='requests'),
    (AppDependencies | where AppRoleName == 'orders-api' and DependencyType == 'PostgreSQL'
        | summarize Samples=count() | extend Signal='postgresql'),
    (AppAvailabilityResults | where AppRoleName == 'orders-api'
        | summarize Samples=count() | extend Signal='availability'),
    (AppExceptions | where AppRoleName == 'orders-api' | summarize Samples=count() | extend Signal='exceptions')
| project Signal, Samples
"""
    expected = {"heartbeat", "cpu", "requests", "postgresql", "availability"}
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
    server_id = required(values, "POSTGRESQL_SERVER_RESOURCE_ID")
    if not re.fullmatch(
            r"/subscriptions/[0-9a-fA-F-]+/resourceGroups/[\w.()-]+/"
            r"providers/Microsoft.DBforPostgreSQL/flexibleServers/[\w-]+",
            server_id):
        raise DeploymentError("Unexpected PostgreSQL server resource identifier.")
    witness_path = state_directory(values) / "persistence-witness.json"
    witness = None
    if witness_path.exists():
        existing = json.loads(witness_path.read_text(encoding="utf-8"))
        if not isinstance(existing, dict):
            raise DeploymentError("Persistence witness is invalid.")
        if "postgresqlServerResourceId" not in existing:
            progress(
                "[restart] Replacing a pre-PostgreSQL persistence witness.")
        elif existing["postgresqlServerResourceId"].casefold() != server_id.casefold():
            raise DeploymentError(
                "Persistence witness belongs to a different PostgreSQL server. "
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
        witness = {
            "orderId": created["orderId"],
            "order": order,
            "postgresqlServerResourceId": server_id,
        }
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
    if (before["database"]["serverVersion"] != after["database"]["serverVersion"]
            or before["database"]["migrationVersion"]
            != after["database"]["migrationVersion"]):
        raise DeploymentError(
            "PostgreSQL version or migration state changed across the VM restart.")
    result = {
        "ok": True,
        "orderId": witness["orderId"],
        "beforeBootId": before["bootId"],
        "afterBootId": after["bootId"],
        "postgresqlServerResourceId": server_id,
    }
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
        "validate", "prepare", "preprovision", "postprovision", "predown", "export",
        "smoke", "inspect", "fault", "health-check", "knowledge", "incident-workflow", "telemetry",
        "verify-restart",
        "benchmark",
    ))
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--source-repository")
    parser.add_argument("--github-repository")
    parser.add_argument("--outlook-connector")
    parser.add_argument("--email-to", action="append", default=[])
    args = parser.parse_args()
    options = (
        args.source_repository, args.github_repository, args.outlook_connector, args.email_to)
    settings = incident_settings(*options) if any(options) else None
    if settings is not None and args.command != "incident-workflow":
        raise DeploymentError("Incident destination options are valid only for incident-workflow install.")
    direct = {"validate": validate, "prepare": prepare, "preprovision": preprovision,
              "postprovision": postprovision, "predown": predown, "benchmark": benchmark}
    if args.command in direct:
        if args.arguments:
            raise DeploymentError(f"{args.command} does not accept positional arguments.")
        direct[args.command]()
        return
    values = environment()
    if args.command == "fault":
        result = fault(values, args.arguments[0] if args.arguments else "status", args.arguments[1:])
    elif args.command == "health-check":
        if len(args.arguments) > 1:
            raise DeploymentError("health-check accepts one action: install, status, or remove.")
        result = health_check(values, args.arguments[0] if args.arguments else "status")
    elif args.command == "knowledge":
        if len(args.arguments) > 1:
            raise DeploymentError("knowledge accepts one action: install, status, or remove.")
        result = knowledge(values, args.arguments[0] if args.arguments else "status")
    elif args.command == "incident-workflow":
        if len(args.arguments) > 1:
            raise DeploymentError("incident-workflow accepts one action: install, status, or remove.")
        result = incident_workflow(
            values, args.arguments[0] if args.arguments else "status", settings)
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
