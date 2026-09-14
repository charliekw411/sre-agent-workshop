#!/usr/bin/env python3
"""Shared azd hooks and fault client. Never print credentials or CLI response bodies."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import yaml


ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agent"
API_VERSION = "2025-05-01-preview"
SAFE_OUTPUTS = {
    "AZURE_ENV_NAME", "AZURE_LOCATION", "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP", "AZURE_CONTAINER_REGISTRY_ENDPOINT",
    "AZURE_CONTAINER_REGISTRY_NAME", "WORKSHOP_SUFFIX", "LOCATION",
    "RESOURCE_GROUP", "SUBSCRIPTION_ID", "TENANT_ID", "ACR_NAME",
    "ACR_LOGIN_SERVER", "LOG_ANALYTICS_NAME", "LOG_ANALYTICS_ID",
    "LOG_ANALYTICS_CUSTOMER_ID", "APP_INSIGHTS_NAME", "SQL_SERVER_NAME",
    "SQL_DATABASE_NAME", "CONTAINER_ENV_NAME", "ORDERS_API_FQDN",
    "SERVICE_ORDERS_API_ENDPOINT_URL", "BOOTSTRAP_JOB_NAME", "KEY_VAULT_NAME",
    "SRE_AGENT_NAME", "SRE_AGENT_RESOURCE_ID", "SRE_AGENT_ENDPOINT",
    "SRE_AGENT_PRINCIPAL_ID", "SRE_AGENT_IDENTITY_RESOURCE_ID",
    "SRE_AGENT_IDENTITY_PRINCIPAL_ID", "KEY_VAULT_URI", "FAULT_TOKEN_SECRET_URI",
    "ORDERS_IDENTITY_PRINCIPAL_ID", "ORDERS_IDENTITY_CLIENT_ID", "ORDERS_IDENTITY_RESOURCE_ID",
}
LEGACY_SECRETS = {"SQL_ADMIN_PASSWORD", "SQL_ADMIN_LOGIN", "FAULT_TOKEN"}
PROVIDERS = {
    "Microsoft.App", "Microsoft.ContainerRegistry", "Microsoft.OperationalInsights",
    "Microsoft.Insights", "Microsoft.Sql", "Microsoft.ManagedIdentity",
    "Microsoft.KeyVault", "Microsoft.AlertsManagement",
    "Microsoft.ContainerInstance", "Microsoft.Storage",
}


class DeploymentError(Exception):
    pass


class ApiError(DeploymentError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"Azure API returned HTTP {status}; response omitted to protect credentials.")


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise DeploymentError("YAML keys must be unique strings.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)


def keys(value, expected, context):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise DeploymentError(f"{context} must contain exactly: {', '.join(expected)}.")


def name(value, document=False):
    pattern = r"workshop-[a-z0-9-]+\.md" if document else r"workshop-[a-z0-9-]+"
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise DeploymentError("Managed names must start with workshop- and use lowercase letters/digits/hyphens.")
    return value


def read_source(source):
    if not isinstance(source, str) or not source:
        raise DeploymentError("Knowledge source must be a repository-relative Markdown path.")
    path = (AGENT / source).resolve()
    if (not path.is_relative_to(AGENT.resolve())
            or Path(source).parts[0] not in {"instructions", "runbooks"}
            or path.suffix != ".md" or not path.is_file()):
        raise DeploymentError("Knowledge sources must be Markdown files inside agent/instructions or agent/runbooks.")
    if path.stat().st_size > 16 * 1024 * 1024:
        raise DeploymentError("Knowledge file exceeds the 16 MB API limit.")
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise DeploymentError("Knowledge sources must not be empty.")
    return content


def load_config():
    try:
        filters = yaml.load((AGENT / "incident-filters.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
        knowledge = yaml.load((AGENT / "knowledge.yaml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (yaml.YAMLError, UnicodeError, OSError) as error:
        raise DeploymentError("Cannot parse the agent YAML files.") from error
    keys(filters, ("version", "filters"), "Incident filter manifest")
    keys(knowledge, ("version", "instructions", "documents"), "Knowledge manifest")
    if type(filters["version"]) is not int or filters["version"] != 1 or type(knowledge["version"]) is not int or knowledge["version"] != 1:
        raise DeploymentError("Only manifest version 1 is supported.")
    for values in (filters["filters"], knowledge["instructions"], knowledge["documents"]):
        if not isinstance(values, list) or not values:
            raise DeploymentError("Manifest sections must be nonempty lists.")
    seen = set()
    for item in filters["filters"]:
        keys(item, ("metadata", "spec"), "Incident filter")
        keys(item["metadata"], ("name",), "Filter metadata")
        identifier = name(item["metadata"]["name"])
        if identifier in seen:
            raise DeploymentError("Duplicate incident filter name.")
        seen.add(identifier)
        spec = item["spec"]
        keys(spec, ("incidentPlatform", "isEnabled", "priorities", "handlingAgent",
                    "agentMode", "deepInvestigationEnabled", "maxAutomatedInvestigationAttempts"), "Filter spec")
        if spec["incidentPlatform"] != "AzMonitor" or spec["handlingAgent"] != "default" or spec["agentMode"] != "Review":
            raise DeploymentError("Workshop filters require AzMonitor, default handling agent, and Review mode.")
        if type(spec["isEnabled"]) is not bool or type(spec["deepInvestigationEnabled"]) is not bool:
            raise DeploymentError("Filter flags must be YAML booleans.")
        priorities = spec["priorities"]
        if (not isinstance(priorities, list) or not priorities
                or any(not isinstance(p, str) or p not in {"Sev0", "Sev1", "Sev2", "Sev3", "Sev4"} for p in priorities)
                or len(set(priorities)) != len(priorities)):
            raise DeploymentError("Filter priorities must be unique Sev0 through Sev4 values.")
        attempts = spec["maxAutomatedInvestigationAttempts"]
        if type(attempts) is not int or not 1 <= attempts <= 3:
            raise DeploymentError("Investigation attempts must be between 1 and 3.")
    for section in ("instructions", "documents"):
        seen = set()
        for item in knowledge[section]:
            keys(item, ("name", "source"), section)
            identifier = name(item["name"], document=section == "documents")
            if identifier in seen:
                raise DeploymentError("Duplicate knowledge name.")
            seen.add(identifier)
            item["content"] = read_source(item["source"])
    return filters["filters"], knowledge


def cli(tool, *args):
    executable = shutil.which(tool)
    if not executable:
        raise DeploymentError(f"Install {tool} before running azd up.")
    environment = os.environ.copy()
    environment.update({
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "AZURE_CORE_LOG_LEVEL": "error",
        "AZURE_LOG_LEVEL": "error",
        "AZURE_CORE_ENABLE_LOG_FILE": "false",
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
    })
    command = [executable, *args]
    if tool == "az":
        command += ["--only-show-errors", "--output", "json"]
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True)
    if result.returncode:
        raise DeploymentError(f"{tool} {args[0]} failed (exit {result.returncode}); captured output withheld.")
    return result.stdout


def az(*args):
    output = cli("az", *args)
    return json.loads(output) if output.strip() else None


def environment():
    values = json.loads(cli("azd", "env", "get-values", "--output", "json"))
    if not isinstance(values, dict):
        raise DeploymentError("azd did not return environment values.")
    subscription = values.get("AZURE_SUBSCRIPTION_ID") or values.get("SUBSCRIPTION_ID")
    if not subscription:
        raise DeploymentError("Select an Azure subscription in the azd environment.")
    uuid.UUID(subscription)
    az("account", "set", "--subscription", subscription)
    return values


def required(values, key):
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeploymentError(f"Missing deployment output {key}; run azd up.")
    return value


def export_values(values):
    directory = ROOT / ".workshop"
    directory.mkdir(exist_ok=True)
    safe = {key: str(value) for key, value in values.items() if key in SAFE_OUTPUTS and value is not None}
    for filename, content in (
        ("workshop.env", "".join(f"export {key}={shlex.quote(value)}\n" for key, value in sorted(safe.items()))),
        ("workshop.ps1", "".join(f"$env:{key} = '{value.replace(chr(39), chr(39) * 2)}'\n" for key, value in sorted(safe.items()))),
    ):
        path = directory / filename
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)


def register_providers():
    def states():
        return {item["namespace"]: item["state"] for item in az(
            "provider", "list", "--query", "[].{namespace:namespace,state:registrationState}"
        )}

    current = states()
    pending = {provider for provider in PROVIDERS if current.get(provider) != "Registered"}
    for provider in sorted(pending):
        az("provider", "register", "--namespace", provider)
    for _ in range(90):
        if not pending:
            return
        time.sleep(10)
        current = states()
        pending = {provider for provider in pending if current.get(provider) != "Registered"}
    raise DeploymentError("Resource provider registration did not complete within fifteen minutes.")


def token_identity(token):
    segment = token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    return str(uuid.UUID(claims["oid"])), str(uuid.UUID(claims["tid"]))


def prepare():
    load_config()
    values = environment()
    # Remove credentials left by earlier workshop revisions without echoing them.
    env_name = required(values, "AZURE_ENV_NAME")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", env_name):
        raise DeploymentError("Unexpected azd environment name.")
    env_file = ROOT / ".azure" / env_name / ".env"
    if env_file.exists():
        lines = env_file.read_text(encoding="utf-8").splitlines(keepends=True)
        env_file.write_text("".join(line for line in lines if line.partition("=")[0].strip() not in LEGACY_SECRETS), encoding="utf-8")
        env_file.chmod(0o600)
    config_file = env_file.with_name("config.json")
    if config_file.exists():
        config = json.loads(config_file.read_text(encoding="utf-8"))
        parameters = config.get("infra", {}).get("parameters", {})
        for key in ("sqlAdminPassword", "sqlAdminLogin", "faultToken"):
            parameters.pop(key, None)
        config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        config_file.chmod(0o600)
    for key in LEGACY_SECRETS:
        os.environ.pop(key, None)
        values.pop(key, None)
    export_values(values)
    token = az("account", "get-access-token", "--resource", "https://management.azure.com/")["accessToken"]
    azd_token = json.loads(cli("azd", "auth", "token", "--scope", "https://management.azure.com/.default", "--output", "json"))["token"]
    if token_identity(token) != token_identity(azd_token):
        raise DeploymentError("Azure CLI and azd must be signed into the same principal and tenant.")
    register_providers()
    locations = az("provider", "show", "--namespace", "Microsoft.App",
                   "--query", "resourceTypes[?resourceType=='agents'].locations | [0]")
    location = required(values, "AZURE_LOCATION")
    if not isinstance(locations, list) or location.lower() not in {item.replace(" ", "").lower() for item in locations}:
        raise DeploymentError("The selected Azure region does not advertise SRE Agent support; select a supported region such as eastus2.")
    print("Validated agent configuration and deployment identity; no credentials exported.")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise DeploymentError("Redirect refused for an authenticated request.")


def trusted_url(url, suffix):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or not parsed.hostname.endswith(suffix)
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.query or parsed.fragment):
        raise DeploymentError("Unexpected Azure endpoint; refusing to send credentials.")
    return url.rstrip("/")


def http(method, url, headers=None, body=None):
    request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=120) as response:
            data = response.read()
            if not data:
                return None
            try:
                return json.loads(data)
            except json.JSONDecodeError:
                return data.decode("utf-8")
    except urllib.error.HTTPError as error:
        raise ApiError(error.code) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise DeploymentError("Azure endpoint unavailable; no response body logged.") from error


class AgentClient:
    def __init__(self, endpoint):
        self.endpoint = trusted_url(endpoint, ".azuresre.ai")

    def request(self, method, path, body=None, content_type="application/json"):
        # Refresh tokens on every request; deployment/indexing can exceed token lifetime.
        token = az("account", "get-access-token", "--resource", "https://azuresre.dev")["accessToken"]
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body).encode("utf-8")
        return http(method, self.endpoint + path, {
            "Authorization": "Bearer " + token, "Content-Type": content_type,
        }, body)


def retry(operation, attempts=20, delay=15):
    for attempt in range(attempts):
        try:
            return operation()
        except ApiError as error:
            if error.status not in {401, 403, 404, 409, 429, 500, 502, 503, 504} or attempt == attempts - 1:
                raise
            time.sleep(delay)


def collection(response, field="value"):
    items = response.get(field, response.get("value")) if isinstance(response, dict) else response
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise DeploymentError("Unexpected SRE Agent list response; refusing partial reconciliation.")
    return items


def reconcile_extended(client, route, kind, desired):
    existing = collection(retry(lambda: client.request("GET", f"/api/v2/extendedAgent/{route}")))
    wanted = {item["name"] for item in desired}
    for item in existing:
        identifier = item.get("name", "")
        if identifier.startswith("workshop-") and identifier not in wanted:
            if route == "incidentFilters":
                # v2 filter DELETE is not documented. Fail closed rather than guess a route.
                raise DeploymentError("A retired workshop filter exists. Keep its YAML entry with isEnabled: false; filter deletion is not automated.")
            retry(lambda: client.request("DELETE", f"/api/v2/extendedAgent/{route}/{urllib.parse.quote(identifier, safe='')}"))
    for item in desired:
        identifier = item["name"]
        path = f"/api/v2/extendedAgent/{route}/{urllib.parse.quote(identifier, safe='')}"
        retry(lambda: client.request("PUT", path, {"name": identifier, "type": kind, "tags": [], "properties": item["properties"]}))
    for _ in range(20):
        actual = collection(retry(lambda: client.request("GET", f"/api/v2/extendedAgent/{route}")))
        matches_all = True
        for expected in desired:
            matches = [item for item in actual if item.get("name") == expected["name"]]
            if len(matches) != 1 or any(matches[0].get("properties", {}).get(k) != v for k, v in expected["properties"].items()):
                matches_all = False
                break
        if matches_all:
            return
        time.sleep(5)
    raise DeploymentError(f"SRE Agent {route} read-back did not match the checked-in configuration.")


def document_name(item):
    return item.get("filename") or item.get("fileName") or item.get("name")


def sync_documents(client, documents):
    existing = collection(retry(lambda: client.request("GET", "/api/v1/AgentMemory/files")), "files")
    # Delete only this workshop's owned filenames, including retired entries.
    filenames = {document_name(item) for item in existing}
    for filename in filenames:
        if isinstance(filename, str) and re.fullmatch(r"workshop-[a-z0-9-]+\.md", filename):
            try:
                retry(lambda: client.request("DELETE", "/api/v1/AgentMemory/document/" + urllib.parse.quote(filename, safe="")))
            except ApiError as error:
                if error.status != 404:
                    raise
    for _ in range(30):
        remaining = collection(retry(lambda: client.request("GET", "/api/v1/AgentMemory/files")), "files")
        if not any(isinstance(document_name(item), str) and re.fullmatch(r"workshop-[a-z0-9-]+\.md", document_name(item)) for item in remaining):
            break
        time.sleep(5)
    else:
        raise DeploymentError("Retired knowledge documents are still present; refusing duplicate uploads.")
    for index, document in enumerate(documents):
        boundary = "workshop-" + uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"{document['name']}\"\r\n"
                "Content-Type: text/markdown\r\n\r\n").encode() + document["content"].encode("utf-8") + f"\r\n--{boundary}--\r\n".encode()
        # Do not blindly retry POST: a lost response may already have created the file.
        # A rerun begins with scoped deletion, restoring deterministic state.
        trigger = "true" if index == len(documents) - 1 else "false"
        client.request("POST", "/api/v1/AgentMemory/upload?triggerIndexing=" + trigger, body, f"multipart/form-data; boundary={boundary}")
    desired = {item["name"] for item in documents}
    for _ in range(60):
        actual = collection(retry(lambda: client.request("GET", "/api/v1/AgentMemory/files")), "files")
        owned = [item for item in actual if document_name(item) in desired]
        names = [document_name(item) for item in owned]
        if len(names) == len(desired) and set(names) == desired and all(
            item.get("isIndexed") is True or str(item.get("indexStatus", "")).lower() == "indexed" for item in owned
        ):
            return
        time.sleep(10)
    raise DeploymentError("Knowledge documents did not reach a unique indexed state within ten minutes; rerun azd up.")


def configure_agent(values=None):
    filters, knowledge = load_config()
    values = values or environment()
    agent_id = required(values, "SRE_AGENT_RESOURCE_ID")
    if not re.fullmatch(r"/subscriptions/[0-9a-fA-F-]+/resourceGroups/[\w.()-]+/providers/Microsoft.App/agents/[\w-]+", agent_id):
        raise DeploymentError("Unexpected SRE Agent resource ID.")
    arm_url = f"https://management.azure.com{agent_id}?api-version={API_VERSION}"
    # Platform initialization is a documented ARM operation, not an action-group webhook.
    resource = az("rest", "--method", "get", "--url", arm_url)
    if (resource["properties"].get("incidentManagementConfiguration") or {}).get("type") != "AzMonitor":
        az("rest", "--method", "patch", "--url", arm_url, "--body",
           json.dumps({"properties": {"incidentManagementConfiguration": {"type": "AzMonitor"}}}))
        time.sleep(30)
        resource = az("rest", "--method", "get", "--url", arm_url)
    client = AgentClient(resource["properties"]["agentEndpoint"])
    reconcile_extended(client, "commonprompts", "CommonPrompt", [
        {"name": item["name"], "properties": {"prompt": item["content"]}} for item in knowledge["instructions"]
    ])
    sync_documents(client, knowledge["documents"])
    reconcile_extended(client, "incidentFilters", "IncidentFilter", [
        {"name": item["metadata"]["name"], "properties": item["spec"]} for item in filters
    ])
    print("SRE Agent instructions, indexed knowledge, and incident filters applied and verified.")


def bootstrap(values):
    group = required(values, "RESOURCE_GROUP")
    job = required(values, "BOOTSTRAP_JOB_NAME")
    app = az("containerapp", "show", "--name", "orders-api", "--resource-group", group)
    image = app["properties"]["template"]["containers"][0]["image"]
    az("containerapp", "job", "update", "--name", job, "--resource-group", group, "--image", image)
    execution = az("containerapp", "job", "start", "--name", job, "--resource-group", group)
    execution_name = execution["name"].rsplit("/", 1)[-1]
    for _ in range(120):
        result = az("containerapp", "job", "execution", "show", "--name", job,
                    "--resource-group", group, "--job-execution-name", execution_name)
        status = result["properties"]["status"]
        if status == "Succeeded":
            print("Database bootstrap succeeded; schema, runtime grants, and seed records verified.")
            return
        if status in {"Failed", "Stopped", "Degraded"}:
            raise DeploymentError("Database bootstrap job failed; inspect its Azure logs, then rerun azd up.")
        time.sleep(10)
    raise DeploymentError("Database bootstrap job timed out after twenty minutes.")


def verify_app(values):
    url = trusted_url("https://" + required(values, "ORDERS_API_FQDN"), ".azurecontainerapps.io")
    for attempt in range(30):
        try:
            orders = http("GET", url + "/orders")
            if not isinstance(orders, list):
                raise DeploymentError("Orders API returned an unexpected response.")
            print("Orders API can read workshop data using managed identity.")
            return
        except DeploymentError:
            if attempt == 29:
                raise
            time.sleep(10)


def fault(action, arguments):
    values = environment()
    url = trusted_url("https://" + required(values, "ORDERS_API_FQDN"), ".azurecontainerapps.io")
    actions = {
        "cpu": ("cpu", ("seconds", "threads"), (600, 4)),
        "errors": ("errors", ("ratePercent", "ttlSeconds"), (100, 900)),
        "storage": ("storage", ("targetPercent",), (95,)),
        "release": ("storage/release", (), ()),
        "reset": ("reset", (), ()),
        "status": ("status", (), ()),
    }
    path, fields, defaults = actions[action]
    if len(arguments) > len(fields):
        raise DeploymentError("Too many fault arguments.")
    numbers = [int(value) for value in arguments]
    parameters = dict(zip(fields, numbers + list(defaults[len(numbers):])))
    token = az("keyvault", "secret", "show", "--vault-name", required(values, "KEY_VAULT_NAME"),
               "--name", "fault-token", "--query", "value")
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-fA-F]{48,128}", token):
        raise DeploymentError("Fault credential is unavailable or has an unexpected format.")
    result = http("GET" if action == "status" else "POST", url + "/fault/" + path,
                  {"X-Fault-Token": token, "Content-Type": "application/json"},
                  None if action == "status" else json.dumps(parameters).encode())
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "prepare", "postdeploy", "configure-agent", "export", "fault"))
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    if args.command == "validate":
        load_config()
        print("Agent YAML and Markdown references are valid.")
    elif args.command == "prepare":
        prepare()
    elif args.command == "configure-agent":
        configure_agent()
    elif args.command == "fault":
        action = args.arguments[0] if args.arguments else "status"
        if action not in {"cpu", "errors", "storage", "release", "reset", "status"}:
            raise DeploymentError("Unknown fault action.")
        fault(action, args.arguments[1:])
    else:
        values = environment()
        if args.command == "postdeploy":
            load_config()
            bootstrap(values)
            verify_app(values)
            configure_agent(values)
        export_values(values)
        print("Non-sensitive attendee outputs exported to .workshop/workshop.env and workshop.ps1.")


if __name__ == "__main__":
    try:
        main()
    except DeploymentError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, ValueError, OSError, TypeError, AttributeError, IndexError, RecursionError):
        print("ERROR: Unexpected configuration or Azure response; details withheld to protect credentials.", file=sys.stderr)
        sys.exit(1)
