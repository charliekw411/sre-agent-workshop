---
title: Troubleshooting
description: Diagnose deployment, VM service, SQLite, telemetry, alert, fault, SRE Agent, and cleanup problems in the single-VM workshop.
ms.date: 2026-09-24
ms.topic: troubleshooting
keywords:
  - troubleshooting
  - azure virtual machines
  - azure monitor
  - azure sre agent
estimated_reading_time: 16
---

## How to use this page

Start with the failed workshop command and its preserved evidence under
`.workshop/<environment>/`. Do not work around a failure by opening SSH, adding a
public fault endpoint, moving SQLite to the OS disk, or widening the SRE Agent's
permissions.

Load the selected environment before using the examples:

```bash
source .workshop/workshop.env
```

PowerShell users load `. ./.workshop/workshop.ps1` and replace `${NAME}` with
`$env:NAME`.

## Deployment problems

### Azure CLI and azd use different identities or subscriptions

The preflight check compares the identity and tenant used by both tools. Sign in
again and select the same subscription:

```bash
az login
az account set --subscription "<subscription-id>"
azd auth login
az account show --output table
azd env get-value AZURE_SUBSCRIPTION_ID
```

Do not bypass the check. A mismatched deployment can create resources under one
identity and then fail role assignment or post-provision operations under another.

### An existing resource group is rejected

The current workshop requires the resource-group tag
`workshop-architecture=single-vm`. It does not migrate an earlier architecture.
Create a fresh azd environment:

```bash
azd env new "<your-alias>-sre-vm-aue"
azd env set AZURE_LOCATION australiaeast
azd up
```

Review and remove the old environment separately. Do not delete individual
resources to trick preflight into treating an old group as compatible.

### The selected VM size is unavailable

Preflight checks x64 Generation 2 compatibility, regional SKU restrictions, and
quota. It never requests more quota or silently changes size.

Choose an available, non-burstable x64 size with at least 4 GiB RAM:

```bash
azd env set VM_SIZE "<available-size>"
azd up
```

One-GiB and ARM64 free-tier sizes are not validated. Burstable B-series credit
behavior can make the CPU incident misleading.

### Policy blocks the deployment

The workshop requires:

* A public Standard IP and inbound TCP 8080.
* Outbound access for Ubuntu packages, NuGet, and Azure monitoring.
* Managed disks, VM extensions, Log Analytics, Application Insights, and the
  preview Azure SRE Agent resource.

No policy exemptions are created. Use an approved subscription or change the
workshop design outside this lab; do not add bypass tags or weaken an
organization's policy.

### SRE Agent is unavailable in the region

The preview service is region constrained. Use the documented default
`australiaeast` unless preflight confirms another region. Model availability can
also vary by subscription.

### `azd up` provisioned resources but failed later

Read the stage named in the error and inspect:

```bash
Get-ChildItem ".workshop/${AZURE_ENV_NAME}"  # PowerShell
```

or:

```bash
find ".workshop/${AZURE_ENV_NAME}" -maxdepth 1 -type f -print
```

Correct the cause and rerun `azd up`. The workflow preserves the existing data
disk and orders. Do not delete the disk as a generic retry step.

## VM and application problems

### The public API is unreachable

Check the URL, public IP, VM power state, and NSG:

```bash
echo "${SERVICE_ORDERS_API_ENDPOINT_URL}"
az vm get-instance-view \
  --resource-group "${RESOURCE_GROUP}" \
  --name "${VM_NAME}" \
  --query "instanceView.statuses[].displayStatus" \
  --output table
az network nsg list --resource-group "${RESOURCE_GROUP}" --output table
```

Only TCP 8080 is allowed inbound. Port 22 is intentionally blocked.

Run the supported inspection:

```bash
python scripts/workshop.py inspect
```

If the service itself needs inspection, use authenticated Run Command:

```bash
az vm run-command invoke \
  --resource-group "${RESOURCE_GROUP}" \
  --name "${VM_NAME}" \
  --command-id RunShellScript \
  --scripts "systemctl status orders-api --no-pager; journalctl -u orders-api -n 100 --no-pager" \
  --output json
```

Do not expose SSH as a troubleshooting shortcut.

### Readiness returns 503 or inspection reports a missing mount

`orders-api.service` requires `/var/lib/orders`. Check the mount and disk:

```bash
az vm run-command invoke \
  --resource-group "${RESOURCE_GROUP}" \
  --name "${VM_NAME}" \
  --command-id RunShellScript \
  --scripts "findmnt /var/lib/orders; lsblk -f; systemctl status orders-api --no-pager" \
  --output json
```

The filesystem must be ext4 on the Azure data disk at LUN 0. Do not point the
connection string at the OS disk. Rerun `azd up` after correcting an actual
deployment failure.

### SQLite operations return HTTP 503

The API maps `SqliteException` to HTTP 503 and records dependency and exception
telemetry. Check:

```bash
curl --silent "${SERVICE_ORDERS_API_ENDPOINT_URL}/storage" | jq .
python scripts/workshop.py fault status
python scripts/workshop.py inspect
```

If a disk fault is active, reset it:

```bash
python scripts/workshop.py fault reset
```

Then use Application Insights **Failures** and query `AppExceptions` for the
SQLite error code. Do not delete `orders.db` or its WAL files.

### `/fault/*` returns 404

That is expected. Public destructive fault endpoints do not exist in the
single-VM architecture. Use:

```bash
python scripts/workshop.py fault cpu 600 2
python scripts/workshop.py fault disk 90 600
python scripts/workshop.py fault reset
```

## Fault problems

### Run Command timed out

A client timeout does not prove the guest action failed. Inspect before retrying:

```bash
python scripts/workshop.py fault status
```

If the requested fault is active, continue the exercise. If state is ambiguous,
inspect Azure VM Run Command operation history and the saved fault JSON. Do not
submit duplicate pressure blindly.

### CPU or disk fault says one is already active

Reset the current transient units:

```bash
python scripts/workshop.py fault reset
python scripts/workshop.py fault status
```

The fault implementation rejects overlapping units so the resulting chart has
one bounded cause.

### Disk pressure is refused

The safety checks reject an unexpected mount, wrong device, existing ballast,
invalid target, or allocation that would violate the recovery reserve. Run:

```bash
python scripts/workshop.py inspect
python scripts/workshop.py fault status
```

Resolve the stated condition. Never edit `faults.py` to remove mount or reserve
checks on a deployed environment.

## Telemetry and portal problems

### The VM CPU chart has no recent data

Confirm the VM is running and the chart uses:

* VM resource > **Monitoring** > **Metrics**
* Metric **Percentage CPU**
* Aggregation **Average**
* Time range **Last 30 minutes**
* Time granularity **1 minute**

Platform metrics can lag. Generate endpoint traffic, wait two minutes, and
refresh. Verify the selected portal subscription and resource group.

### `python scripts/workshop.py telemetry` times out

The command reports missing signals. Check Azure Monitor Agent and the data
collection rule association:

```bash
az vm extension list \
  --resource-group "${RESOURCE_GROUP}" \
  --vm-name "${VM_NAME}" \
  --query "[].{Name:name, State:provisioningState}" \
  --output table

az rest \
  --method get \
  --uri "https://management.azure.com${VM_RESOURCE_ID}/providers/Microsoft.Insights/dataCollectionRuleAssociations?api-version=2023-03-11" \
  --query "value[].{Name:name, Rule:properties.dataCollectionRuleId}" \
  --output table
```

Call `/orders` and `/storage`, wait for ingestion, and retry. Application
Insights and guest `Perf` commonly arrive later than the VM platform metric.

### Application Insights has no requests

```bash
curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/orders" > /dev/null
curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/storage" > /dev/null
```

Wait several minutes, then query:

```kusto
AppRequests
| where AppRoleName == "orders-api"
| where TimeGenerated > ago(30m)
| order by TimeGenerated desc
```

Check that you opened the `appi-<suffix>` resource from the selected workshop
group, not another Application Insights instance.

### The data-disk alert does not fire

The DCR samples once per minute and the rule evaluates a five-minute window.
Confirm the exact dimension:

```kusto
Perf
| where TimeGenerated > ago(30m)
| where ObjectName == "Logical Disk"
| where CounterName == "% Free Space"
| summarize Samples=count(), Minimum=min(CounterValue) by InstanceName
```

The expected `InstanceName` is `/var/lib/orders`. If it never falls below 15,
inspect the fault state and `/storage` rather than lowering the alert threshold
during the exercise.

### The CPU alert does not fire

Confirm the VM **Percentage CPU** chart stayed above 80 percent for a five-minute
average. A burst shorter than the evaluation window should not fire. Use the
documented ten-minute fault and enough workers for the selected VM size.

## Azure SRE Agent problems

### The response plan is missing

Check the local configuration evidence:

```bash
cat ".workshop/${AZURE_ENV_NAME}/sre-agent-configuration.json"
```

Rerun `azd up` to reconcile deployment-managed configuration. Do not create a
second broad response plan manually.

### An alert fired but no investigation appears

Confirm:

* The alert severity is Sev1 or Sev2.
* `workshop-sev1-sev2-review` is enabled for Azure Monitor.
* The alert belongs to the workshop resource group.
* The response plan is not filtered to another resource.

Allow for service propagation, then refresh the SRE Agent incident view. Keep
investigating from Azure Monitor and raw telemetry while routing is delayed.

### The agent can see resources but cannot query telemetry

Review both agent identities and their resource/workspace role assignments.
They need resource read access and Log Analytics read access, not Contributor.
Role propagation can take several minutes.

### The agent proposes but does not execute a mitigation

That is expected. The workshop uses Review mode and read-only Azure RBAC.
Execute an approved reset through your own authenticated session:

```bash
python scripts/workshop.py fault reset
```

Do not grant write access merely to make the demonstration automatic.

## Cleanup problems

### `azd down --purge` stalls

Check resource-group state and locks:

```bash
az group show --name "${RESOURCE_GROUP}" --query properties.provisioningState
az lock list --resource-group "${RESOURCE_GROUP}" --output table
```

Review policy errors in the Activity Log. Delete only the confirmed workshop
scope; do not use broad or wildcard deletion commands.

<div class="sre-nav" markdown>
[:material-arrow-left: Workshop Variables](01-variables.md)
[Cost Management :material-arrow-right:](03-cost-management.md)
</div>
