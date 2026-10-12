#!/usr/bin/env python3
"""Deploy or clean up isolated workshop environments for pre-created participants."""

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid

import workshop


ROOT = Path(__file__).resolve().parents[1]
STATE_DIRECTORY = ROOT / ".workshop" / "cohort"
STATE_PATH = STATE_DIRECTORY / "state.json"
ROSTER_PATH = STATE_DIRECTORY / "roster.json"
LOG_DIRECTORY = STATE_DIRECTORY / "logs"
ARCHITECTURE = "single-vm-postgresql-v1"
CONTRIBUTOR_ROLE_ID = "b24988ac-6180-42a0-ab88-20f7382dd24c"
STATE_LOCK = threading.Lock()


class CohortError(Exception):
    pass


def progress(message):
    print(message, file=sys.stderr, flush=True)


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def safe_roster(state):
    return {
        "participants": [
            {
                "participant": item["participant"],
                "subscription": item["subscriptionLabel"],
                "environment": item["environment"],
                "resourceGroup": item["resourceGroup"],
                "status": item["status"],
            }
            for item in state["participants"]
        ]
    }


def write_state(state):
    atomic_json(STATE_PATH, state)
    atomic_json(ROSTER_PATH, safe_roster(state))


def save_state(state):
    with STATE_LOCK:
        write_state(state)


def update_item(state, item, **changes):
    with STATE_LOCK:
        item.update(changes)
        write_state(state)


def resolve_subscriptions(requested):
    subscriptions = []
    seen = set()
    for index, value in enumerate(requested, 1):
        account = workshop.az("account", "show", "--subscription", value)
        subscription_id = str(uuid.UUID(account["id"]))
        if subscription_id in seen:
            raise CohortError(f"Subscription {subscription_id} was supplied more than once.")
        seen.add(subscription_id)
        subscriptions.append({
            "id": subscription_id,
            "tenantId": str(uuid.UUID(account["tenantId"])),
            "name": account["name"],
            "label": f"subscription-{index}",
        })
    tenants = {item["tenantId"] for item in subscriptions}
    if len(tenants) != 1:
        raise CohortError(
            "All workshop subscriptions must use the tenant containing the participant users."
        )
    return subscriptions


def participant_records(count, subscriptions, user_prefix, user_domain):
    if not 1 <= count <= 20:
        raise CohortError("--participants must be between 1 and 20.")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", user_prefix):
        raise CohortError("--user-prefix must contain only letters, digits, and internal hyphens.")
    if not re.fullmatch(
            r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", user_domain):
        raise CohortError("--user-domain is not a valid DNS domain.")
    result = []
    for number in range(1, count + 1):
        participant = f"participant-{number:02d}"
        local_part = f"{user_prefix}-{number:02d}"
        if len(local_part) > 64:
            raise CohortError("Generated participant UPN local part exceeds 64 characters.")
        subscription = subscriptions[(number - 1) % len(subscriptions)]
        result.append({
            "number": number,
            "participant": participant,
            "userPrincipalName": f"{local_part}@{user_domain}",
            "userObjectId": None,
            "environment": participant,
            "resourceGroup": f"rg-{number:02d}",
            "subscriptionId": subscription["id"],
            "subscriptionLabel": subscription["label"],
            "deployment": "Pending",
            "validation": "Pending",
            "rbac": "Pending",
            "status": "Pending",
            "attempts": 0,
            "durationSeconds": None,
            "error": None,
        })
    return result


def load_or_create_state(args, subscriptions):
    requested = participant_records(
        args.participants, subscriptions, args.user_prefix, args.user_domain,
    )
    if not STATE_PATH.exists():
        state = {
            "version": 1,
            "location": args.location,
            "vmSize": args.vm_size,
            "userPrefix": args.user_prefix,
            "userDomain": args.user_domain,
            "subscriptions": subscriptions,
            "participants": requested,
            "createdUtc": datetime.now(timezone.utc).isoformat(),
        }
        save_state(state)
        return state

    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    expected = {
        "location": args.location,
        "vmSize": args.vm_size,
        "userPrefix": args.user_prefix,
        "userDomain": args.user_domain,
    }
    for key, value in expected.items():
        if state.get(key) != value:
            raise CohortError(
                f"The saved cohort uses {key}={state.get(key)!r}, not {value!r}."
            )
    saved_subscriptions = [item["id"] for item in state.get("subscriptions", [])]
    requested_subscriptions = [item["id"] for item in subscriptions]
    if saved_subscriptions != requested_subscriptions:
        raise CohortError(
            "Subscription order differs from the saved cohort and would remap participants."
        )
    existing_count = len(state["participants"])
    if args.participants < existing_count:
        raise CohortError(
            f"The saved cohort already contains {existing_count} participants; "
            "a deployment rerun cannot reduce it."
        )
    if args.participants > existing_count:
        state["participants"].extend(requested[existing_count:])
        save_state(state)
    return state


def resolve_users(state):
    object_ids = set()
    for item in state["participants"]:
        user_url = (
            "https://graph.microsoft.com/v1.0/users/"
            + urllib.parse.quote(item["userPrincipalName"], safe="")
            + "?$select=id,userPrincipalName,accountEnabled,userType"
        )
        user = workshop.az(
            "rest",
            "--method", "get",
            "--url", user_url,
            "--resource", "https://graph.microsoft.com",
        )
        if user.get("accountEnabled") is not True:
            raise CohortError(f"{item['userPrincipalName']} is disabled.")
        object_id = str(uuid.UUID(user["id"]))
        if object_id in object_ids:
            raise CohortError("Two participant UPNs resolved to the same Entra object.")
        if item.get("userObjectId") not in (None, object_id):
            raise CohortError(
                f"{item['userPrincipalName']} now resolves to a different Entra object."
            )
        object_ids.add(object_id)
        item["userObjectId"] = object_id


def verify_authentication(state):
    subscription = state["subscriptions"][0]
    token = workshop.az(
        "account", "get-access-token",
        "--resource", "https://management.azure.com/",
        "--subscription", subscription["id"],
        sensitive=True,
    )["accessToken"]
    azd_token = json.loads(workshop.cli(
        "azd", "auth", "token",
        "--tenant-id", subscription["tenantId"],
        "--scope", "https://management.azure.com/.default",
        "--output", "json",
        sensitive=True,
    ))["token"]
    if workshop.token_identity(token) != workshop.token_identity(azd_token):
        raise CohortError("Azure CLI and azd must use the same principal and tenant.")


def inspect_resource_group(item, location, vm_size):
    exists = workshop.az(
        "group", "exists",
        "--name", item["resourceGroup"],
        "--subscription", item["subscriptionId"],
    )
    if type(exists) is not bool:
        raise CohortError("Azure returned an invalid resource-group existence response.")
    if not exists:
        return False
    group = workshop.az(
        "group", "show",
        "--name", item["resourceGroup"],
        "--subscription", item["subscriptionId"],
    )
    tags = group.get("tags") or {}
    if (tags.get("workshop-architecture") != ARCHITECTURE
            or tags.get("azd-env-name") != item["environment"]):
        raise CohortError(
            f"{item['resourceGroup']} already exists without the matching workshop tags."
        )
    if str(group.get("location", "")).replace(" ", "").casefold() != location.casefold():
        raise CohortError(
            f"{item['resourceGroup']} already exists outside {location}."
        )
    virtual_machines = workshop.az(
        "vm", "list",
        "--resource-group", item["resourceGroup"],
        "--subscription", item["subscriptionId"],
    )
    if len(virtual_machines) > 1:
        raise CohortError(
            f"{item['resourceGroup']} contains more than one VM and is not a valid workshop scope."
        )
    if (len(virtual_machines) == 1
            and virtual_machines[0].get("hardwareProfile", {}).get("vmSize", "").casefold()
            != vm_size.casefold()):
        raise CohortError(
            f"{item['resourceGroup']} contains a VM that is not {vm_size}."
        )
    return len(virtual_machines) == 1


def preflight(state):
    progress("[cohort] Resolving participant accounts")
    resolve_users(state)
    verify_authentication(state)
    for subscription in state["subscriptions"]:
        assigned = [
            item for item in state["participants"]
            if item["subscriptionId"] == subscription["id"]
        ]
        progress(
            f"[cohort] Preflighting {subscription['label']} for "
            f"{len(assigned)} participant(s)"
        )
        workshop.register_providers(subscription["id"], state["location"])
        values = {
            "AZURE_LOCATION": state["location"],
            "AZURE_SUBSCRIPTION_ID": subscription["id"],
            "VM_SIZE": state["vmSize"],
        }
        workshop.ensure_public_ip_feature(values)
        workshop.check_sre_agent_region(values)
        workshop.check_postgresql_capacity(values)
        new_instances = 0
        for item in assigned:
            has_vm = inspect_resource_group(
                item, state["location"], state["vmSize"],
            )
            if not has_vm:
                new_instances += 1
                if item["status"] == "Ready":
                    item.update({
                        "deployment": "Pending",
                        "validation": "Pending",
                        "rbac": "Pending",
                        "status": "Pending",
                    })
        if new_instances:
            workshop.check_vm_capacity(
                values, check_existing=False, instances=new_instances,
            )
    save_state(state)
    progress("[cohort] All subscriptions and participant accounts passed preflight")


def azd_environment_names():
    document = json.loads(workshop.cli("azd", "env", "list", "--output", "json"))
    items = document if isinstance(document, list) else document.get("environments", [])
    names = set()
    for item in items:
        if isinstance(item, str):
            names.add(item)
        elif isinstance(item, dict):
            name = item.get("name") or item.get("Name")
            if name:
                names.add(name)
    return names


def set_azd_value(environment, key, value):
    workshop.cli(
        "azd", "env", "set", key, value,
        "--environment", environment,
        "--no-prompt",
    )


def configure_environment(state, item, existing_environments):
    environment = item["environment"]
    if environment not in existing_environments:
        workshop.cli(
            "azd", "env", "new", environment,
            "--subscription", item["subscriptionId"],
            "--location", state["location"],
            "--no-prompt",
        )
        existing_environments.add(environment)
    values = json.loads(workshop.cli(
        "azd", "env", "get-values",
        "--environment", environment,
        "--output", "json",
    ))
    for key, expected in (
        ("AZURE_SUBSCRIPTION_ID", item["subscriptionId"]),
        ("AZURE_LOCATION", state["location"]),
    ):
        actual = values.get(key)
        if actual and actual.casefold() != expected.casefold():
            raise CohortError(
                f"azd environment {environment} has {key}={actual!r}, not {expected!r}."
            )
        if not actual:
            set_azd_value(environment, key, expected)
    resource_group = values.get("WORKSHOP_RESOURCE_GROUP_NAME")
    if resource_group and resource_group != item["resourceGroup"]:
        raise CohortError(
            f"azd environment {environment} targets {resource_group}, "
            f"not {item['resourceGroup']}."
        )
    for output_name in ("RESOURCE_GROUP", "AZURE_RESOURCE_GROUP"):
        deployed_group = values.get(output_name)
        if deployed_group and deployed_group != item["resourceGroup"]:
            raise CohortError(
                f"azd environment {environment} was previously deployed to "
                f"{deployed_group}, not {item['resourceGroup']}."
            )
    if not resource_group:
        set_azd_value(
            environment, "WORKSHOP_RESOURCE_GROUP_NAME", item["resourceGroup"],
        )
    if values.get("VM_SIZE") not in (None, "", state["vmSize"]):
        raise CohortError(
            f"azd environment {environment} uses VM_SIZE={values['VM_SIZE']}, "
            f"not {state['vmSize']}."
        )
    set_azd_value(environment, "VM_SIZE", state["vmSize"])


def run_logged(command, path, environment):
    path.parent.mkdir(parents=True, exist_ok=True)
    process_environment = os.environ.copy()
    process_environment.update({
        "AZURE_CORE_ONLY_SHOW_ERRORS": "true",
        "AZURE_CORE_ENABLE_LOG_FILE": "false",
        "AZURE_CORE_COLLECT_TELEMETRY": "false",
        "AZD_SKIP_UPDATE_CHECK": "true",
        "AZURE_ENV_NAME": environment,
        "WORKSHOP_COHORT_MODE": "1",
    })
    tail = deque(maxlen=30)
    with path.open("a", encoding="utf-8") as log:
        log.write(
            f"\n=== {datetime.now(timezone.utc).isoformat()} "
            f"{' '.join(map(str, command))} ===\n"
        )
        log.flush()
        process = subprocess.Popen(
            [str(value) for value in command],
            cwd=ROOT,
            env=process_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for line in process.stdout:
            log.write(line)
            log.flush()
            tail.append(line.rstrip())
        code = process.wait()
    if code:
        detail = "\n".join(tail)
        raise CohortError(
            f"{Path(str(command[0])).name} exited with code {code}.\n{detail}"
        )


def contributor_assignments(item):
    scope = (
        f"/subscriptions/{item['subscriptionId']}"
        f"/resourceGroups/{item['resourceGroup']}"
    )
    assignments = workshop.az(
        "role", "assignment", "list",
        "--assignee-object-id", item["userObjectId"],
        "--scope", scope,
        "--all",
        "--fill-principal-name", "false",
        "--subscription", item["subscriptionId"],
    )
    return [
        assignment for assignment in assignments
        if str(assignment.get("scope", "")).casefold() == scope.casefold()
        and str(assignment.get("roleDefinitionId", "")).casefold().endswith(
            CONTRIBUTOR_ROLE_ID
        )
    ]


def ensure_contributor(item):
    if contributor_assignments(item):
        return
    scope = (
        f"/subscriptions/{item['subscriptionId']}"
        f"/resourceGroups/{item['resourceGroup']}"
    )
    workshop.az(
        "role", "assignment", "create",
        "--assignee-object-id", item["userObjectId"],
        "--assignee-principal-type", "User",
        "--role", CONTRIBUTOR_ROLE_ID,
        "--scope", scope,
        "--subscription", item["subscriptionId"],
    )
    for _ in range(6):
        if contributor_assignments(item):
            return
        time.sleep(5)
    raise CohortError(
        f"Contributor assignment was not visible for {item['participant']}."
    )


def deploy_participant(state, item):
    started = time.monotonic()
    update_item(state, item, **{
        "attempts": item["attempts"] + 1,
        "deployment": "Running",
        "validation": "Pending",
        "rbac": "Pending",
        "status": "Deploying",
        "error": None,
        "startedUtc": datetime.now(timezone.utc).isoformat(),
    })
    log_path = LOG_DIRECTORY / f"{item['participant']}.log"
    stage = "deployment"
    try:
        progress(
            f"[{item['participant']}] Deploying {item['resourceGroup']} "
            f"({item['subscriptionLabel']}); log: {log_path}"
        )
        run_logged([
            "azd", "up", "--no-prompt", "--environment", item["environment"],
        ], log_path, item["environment"])
        update_item(
            state, item,
            deployment="Succeeded", validation="Running", status="Validating",
        )

        stage = "validation"
        for arguments in (
            ("smoke",),
            ("inspect",),
            ("telemetry",),
            ("fault", "status"),
        ):
            run_logged([
                sys.executable, ROOT / "scripts" / "workshop.py", *arguments,
            ], log_path, item["environment"])
        update_item(
            state, item,
            validation="Passed", rbac="Running", status="Assigning RBAC",
        )

        stage = "rbac"
        ensure_contributor(item)
        update_item(
            state, item,
            rbac="Contributor",
            status="Ready",
            completedUtc=datetime.now(timezone.utc).isoformat(),
        )
        progress(
            f"[{item['participant']}] Ready: {item['userPrincipalName']} "
            f"has Contributor on {item['resourceGroup']}"
        )
    except (CohortError, workshop.DeploymentError, OSError, KeyError, TypeError, ValueError) as error:
        changes = {
            "status": "Failed",
            "error": str(error),
            "completedUtc": datetime.now(timezone.utc).isoformat(),
        }
        if stage == "deployment":
            changes["deployment"] = "Failed"
        elif stage == "validation":
            changes["validation"] = "Failed"
        else:
            changes["rbac"] = "Failed"
        update_item(state, item, **changes)
        progress(f"[{item['participant']}] FAILED during {stage}; see {log_path}")
    finally:
        update_item(
            state, item,
            durationSeconds=round(time.monotonic() - started, 1),
        )


def deploy_parallel(state, items, parallel_per_subscription):
    executors = {
        subscription["id"]: ThreadPoolExecutor(
            max_workers=parallel_per_subscription,
            thread_name_prefix=subscription["label"],
        )
        for subscription in state["subscriptions"]
    }
    futures = []
    try:
        for item in items:
            futures.append(
                executors[item["subscriptionId"]].submit(
                    deploy_participant, state, item,
                )
            )
        for future in as_completed(futures):
            future.result()
    finally:
        for executor in executors.values():
            executor.shutdown(wait=True)


def duration(value):
    if not isinstance(value, (int, float)):
        return "-"
    minutes, seconds = divmod(int(value), 60)
    return f"{minutes}m {seconds:02d}s"


def table(state):
    headings = (
        "Participant", "Subscription", "Resource group",
        "Infrastructure", "Validation", "RBAC", "Status", "Duration",
    )
    rows = [
        (
            item["participant"],
            item["subscriptionLabel"],
            item["resourceGroup"],
            item["deployment"],
            item["validation"],
            item["rbac"],
            item["status"],
            duration(item.get("durationSeconds")),
        )
        for item in state["participants"]
    ]
    widths = [
        max(len(str(value)) for value in (heading, *(row[index] for row in rows)))
        for index, heading in enumerate(headings)
    ]
    lines = [
        "  ".join(heading.ljust(widths[index])
                  for index, heading in enumerate(headings)),
        "  ".join("-" * width for width in widths),
    ]
    lines.extend(
        "  ".join(str(value).ljust(widths[index])
                  for index, value in enumerate(row))
        for row in rows
    )
    return "\n".join(lines)


def deploy(args):
    if not 1 <= args.parallel_per_subscription <= 3:
        raise CohortError("--parallel-per-subscription must be between 1 and 3.")
    subscriptions = resolve_subscriptions(args.subscriptions)
    state = load_or_create_state(args, subscriptions)
    preflight(state)
    existing_environments = azd_environment_names()
    pending = []
    for item in state["participants"]:
        if item["status"] == "Ready":
            try:
                ensure_contributor(item)
            except (CohortError, workshop.DeploymentError) as error:
                update_item(
                    state, item,
                    rbac="Failed", status="Failed", error=str(error),
                )
            else:
                progress(f"[{item['participant']}] Already ready; skipping deployment")
                continue
        try:
            configure_environment(state, item, existing_environments)
        except (CohortError, workshop.DeploymentError) as error:
            update_item(
                state, item,
                deployment="Failed", status="Failed", error=str(error),
            )
            progress(
                f"[{item['participant']}] FAILED while configuring its azd environment"
            )
            continue
        pending.append(item)
    if pending:
        progress(
            f"[cohort] Deploying up to "
            f"{args.parallel_per_subscription * len(state['subscriptions'])} "
            f"environments at once "
            f"({args.parallel_per_subscription} per subscription)"
        )
        deploy_parallel(state, pending, args.parallel_per_subscription)
    print(table(state))
    failures = [item for item in state["participants"] if item["status"] != "Ready"]
    if failures:
        print("\nFailure logs:", file=sys.stderr)
        for item in failures:
            print(
                f"  {item['participant']}: "
                f"{LOG_DIRECTORY / (item['participant'] + '.log')}",
                file=sys.stderr,
            )
        raise CohortError(f"{len(failures)} participant environment(s) are not ready.")
    print(f"\nSafe roster: {ROSTER_PATH}")


def cleanup_target(state, item):
    exists = workshop.az(
        "group", "exists",
        "--name", item["resourceGroup"],
        "--subscription", item["subscriptionId"],
    )
    if exists is False:
        item["status"] = "Cleaned"
        return
    if exists is not True:
        raise CohortError("Azure returned an invalid resource-group existence response.")
    group = workshop.az(
        "group", "show",
        "--name", item["resourceGroup"],
        "--subscription", item["subscriptionId"],
    )
    tags = group.get("tags") or {}
    if (tags.get("workshop-architecture") != ARCHITECTURE
            or tags.get("azd-env-name") != item["environment"]):
        raise CohortError(
            f"Refusing to delete {item['resourceGroup']}: workshop tags do not match."
        )
    progress(f"[{item['participant']}] Deleting {item['resourceGroup']}")
    workshop.az(
        "group", "delete",
        "--name", item["resourceGroup"],
        "--subscription", item["subscriptionId"],
        "--yes",
        "--no-wait",
    )
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        exists = workshop.az(
            "group", "exists",
            "--name", item["resourceGroup"],
            "--subscription", item["subscriptionId"],
        )
        if exists is False:
            item["status"] = "Cleaned"
            return
        time.sleep(15)
    raise CohortError(f"Deletion of {item['resourceGroup']} exceeded 20 minutes.")


def cleanup(args):
    if not args.yes:
        raise CohortError("Cleanup requires --yes.")
    if not STATE_PATH.exists():
        raise CohortError(f"Cohort state does not exist: {STATE_PATH}")
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if args.all:
        targets = state["participants"]
    else:
        selector = args.participant
        if selector and selector.isdigit():
            selector = f"participant-{int(selector):02d}"
        targets = [
            item for item in state["participants"]
            if item["participant"] == selector
        ]
        if not targets:
            raise CohortError(f"Unknown participant: {args.participant}")
    failures = []
    for item in targets:
        try:
            cleanup_target(state, item)
            item.update({
                "deployment": "Removed",
                "validation": "-",
                "rbac": "Removed",
                "error": None,
            })
        except (CohortError, workshop.DeploymentError, OSError, KeyError, TypeError) as error:
            item["status"] = "Cleanup failed"
            item["error"] = str(error)
            failures.append(item)
        save_state(state)
    print(table(state))
    if failures:
        raise CohortError(f"Cleanup failed for {len(failures)} participant environment(s).")


def parser():
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--participants", type=int, required=True)
    deploy_parser.add_argument("--subscriptions", nargs="+", required=True)
    deploy_parser.add_argument("--user-prefix", required=True)
    deploy_parser.add_argument("--user-domain", required=True)
    deploy_parser.add_argument("--location", default="australiaeast")
    deploy_parser.add_argument("--vm-size", default="Standard_D2as_v5")
    deploy_parser.add_argument("--parallel-per-subscription", type=int, default=2)
    cleanup_parser = commands.add_parser("cleanup")
    target = cleanup_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--participant")
    target.add_argument("--all", action="store_true")
    cleanup_parser.add_argument("--yes", action="store_true")
    return root


def main():
    args = parser().parse_args()
    if args.command == "deploy":
        deploy(args)
    else:
        cleanup(args)


if __name__ == "__main__":
    try:
        main()
    except (CohortError, workshop.DeploymentError, OSError, ValueError, KeyError, TypeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(1)
