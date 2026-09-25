---
title: Troubleshooting
description: Diagnose deployment, VM service, SQLite, telemetry, alert, fault, SRE Agent, and cleanup problems in the single-VM workshop.
ms.date: 2026-09-25
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

=== "Bash"

    ```bash
    source .workshop/workshop.env
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    ```

## Deployment problems

### Azure CLI and azd use different identities or subscriptions

The preflight check compares the identity and tenant used by both tools. Sign in
again and select the same subscription:

=== "Bash"

    ```bash
    az login
    az account set --subscription "<subscription-id>"
    azd auth login
    az account show --output table
    azd env get-value AZURE_SUBSCRIPTION_ID
    ```

=== "PowerShell"

    ```powershell
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

=== "Bash"

    ```bash
    azd env new "<your-alias>-sre-vm-aue"
    azd env set AZURE_LOCATION australiaeast
    azd up
    ```

=== "PowerShell"

    ```powershell
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

=== "Bash"

    ```bash
    azd env set VM_SIZE "<available-size>"
    azd up
    ```

=== "PowerShell"

    ```powershell
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

=== "Bash"

    ```bash
    find ".workshop/${AZURE_ENV_NAME}" -maxdepth 1 -type f -print
    ```

=== "PowerShell"

    ```powershell
    Get-ChildItem ".workshop/$env:AZURE_ENV_NAME"
    ```

Correct the cause and rerun `azd up`. The workflow preserves the existing data
disk and orders. Do not delete the disk as a generic retry step.

## VM and application problems

### The public API or Orders GUI is unreachable

Check the URL, public IP, VM power state, and NSG:

=== "Bash"

    ```bash
    echo "${SERVICE_ORDERS_API_ENDPOINT_URL}"
    az vm get-instance-view \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --query "instanceView.statuses[].displayStatus" \
      --output table
    az network nsg list --resource-group "${RESOURCE_GROUP}" --output table
    ```

=== "PowerShell"

    ```powershell
    $env:SERVICE_ORDERS_API_ENDPOINT_URL
    az vm get-instance-view `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --query "instanceView.statuses[].displayStatus" `
      --output table
    az network nsg list --resource-group $env:RESOURCE_GROUP --output table
    ```

Only TCP 8080 is allowed inbound. Port 22 is intentionally blocked.

Open the exact `SERVICE_ORDERS_API_ENDPOINT_URL` value in a browser. If the page
shell loads but its orders or status cards fail, the VM and static GUI are
reachable and the underlying JSON requests need diagnosis. If the page itself
does not load, continue with the VM, NSG, and service checks below.

Run the supported inspection:

=== "Bash"

    ```bash
    python scripts/workshop.py inspect
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py inspect
    ```

If the service itself needs inspection, use authenticated Run Command:

=== "Bash"

    ```bash
    az vm run-command invoke \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --command-id RunShellScript \
      --scripts "systemctl status orders-api --no-pager; journalctl -u orders-api -n 100 --no-pager" \
      --output json
    ```

=== "PowerShell"

    ```powershell
    az vm run-command invoke `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --command-id RunShellScript `
      --scripts "systemctl status orders-api --no-pager; journalctl -u orders-api -n 100 --no-pager" `
      --output json
    ```

Do not expose SSH as a troubleshooting shortcut.

### The base URL returns JSON instead of the Orders GUI

This is expected from curl, `Invoke-RestMethod`, monitoring probes, and clients
that do not advertise `Accept: text/html`. The root preserves its JSON service
descriptor for backward compatibility. A normal browser address-bar navigation
requests HTML.

Verify HTML negotiation explicitly:

=== "Bash"

    ```bash
    curl --silent --fail \
      --header 'Accept: text/html' \
      --output /dev/null \
      --write-out '%{http_code} %{content_type}\n' \
      "${SERVICE_ORDERS_API_ENDPOINT_URL}/"
    ```

=== "PowerShell"

    ```powershell
    $response = Invoke-WebRequest `
      -Uri "$env:SERVICE_ORDERS_API_ENDPOINT_URL/" `
      -Headers @{ Accept = 'text/html' }
    "$($response.StatusCode) $($response.Headers.'Content-Type')"
    ```

Expect HTTP 200 and `text/html`. If the negotiated request fails after a
deployment, rerun `azd up` so the checksummed application bundle includes the
static assets. Do not add a second web server or Azure service.

### Readiness returns 503 or inspection reports a missing mount

`orders-api.service` requires `/var/lib/orders`. Check the mount and disk:

=== "Bash"

    ```bash
    az vm run-command invoke \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --command-id RunShellScript \
      --scripts "findmnt /var/lib/orders; lsblk -f; systemctl status orders-api --no-pager" \
      --output json
    ```

=== "PowerShell"

    ```powershell
    az vm run-command invoke `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --command-id RunShellScript `
      --scripts "findmnt /var/lib/orders; lsblk -f; systemctl status orders-api --no-pager" `
      --output json
    ```

The filesystem must be ext4 on the Azure data disk at LUN 0. Do not point the
connection string at the OS disk. Rerun `azd up` after correcting an actual
deployment failure.

### SQLite operations return HTTP 503

The API maps `SqliteException` to HTTP 503 and records dependency and exception
telemetry. Check:

=== "Bash"

    ```bash
    curl --silent "${SERVICE_ORDERS_API_ENDPOINT_URL}/storage" | jq .
    python scripts/workshop.py fault status
    python scripts/workshop.py inspect
    ```

=== "PowerShell"

    ```powershell
    Invoke-RestMethod "$env:SERVICE_ORDERS_API_ENDPOINT_URL/storage"
    python scripts/workshop.py fault status
    python scripts/workshop.py inspect
    ```

If a disk fault is active, reset it:

=== "Bash"

    ```bash
    python scripts/workshop.py fault reset
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py fault reset
    ```

Then use Application Insights **Failures** and query `AppExceptions` for the
SQLite error code. Do not delete `orders.db` or its WAL files.

### `/fault/*` returns 404

That is expected. Public destructive fault endpoints do not exist in the
single-VM architecture. Use:

=== "Bash"

    ```bash
    python scripts/workshop.py fault cpu 600 2
    python scripts/workshop.py fault disk 90 600
    python scripts/workshop.py fault reset
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py fault cpu 600 2
    python scripts/workshop.py fault disk 90 600
    python scripts/workshop.py fault reset
    ```

## Fault problems

### Run Command timed out

A client timeout does not prove the guest action failed. Inspect before retrying:

=== "Bash"

    ```bash
    python scripts/workshop.py fault status
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py fault status
    ```

If the requested fault is active, continue the exercise. If state is ambiguous,
inspect Azure VM Run Command operation history and the saved fault JSON. Do not
submit duplicate pressure blindly.

### CPU or disk fault says one is already active

Reset the current transient units:

=== "Bash"

    ```bash
    python scripts/workshop.py fault reset
    python scripts/workshop.py fault status
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py fault reset
    python scripts/workshop.py fault status
    ```

The fault implementation rejects overlapping units so the resulting chart has
one bounded cause.

### Disk pressure is refused

The safety checks reject an unexpected mount, wrong device, existing ballast,
invalid target, or allocation that would violate the recovery reserve. Run:

=== "Bash"

    ```bash
    python scripts/workshop.py inspect
    python scripts/workshop.py fault status
    ```

=== "PowerShell"

    ```powershell
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

=== "Bash"

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

=== "PowerShell"

    ```powershell
    az vm extension list `
      --resource-group $env:RESOURCE_GROUP `
      --vm-name $env:VM_NAME `
      --query "[].{Name:name, State:provisioningState}" `
      --output table

    az rest `
      --method get `
      --uri "https://management.azure.com$($env:VM_RESOURCE_ID)/providers/Microsoft.Insights/dataCollectionRuleAssociations?api-version=2023-03-11" `
      --query "value[].{Name:name, Rule:properties.dataCollectionRuleId}" `
      --output table
    ```

Call `/orders` and `/storage`, wait for ingestion, and retry. Application
Insights and guest `Perf` commonly arrive later than the VM platform metric.

### Application Insights has no requests

Opening the Orders GUI and selecting **Refresh orders** and **Refresh status**
generates the same underlying API operations. Use the commands below when you
need a small, deterministic troubleshooting sample:

=== "Bash"

    ```bash
    curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/orders" > /dev/null
    curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/storage" > /dev/null
    ```

=== "PowerShell"

    ```powershell
    Invoke-RestMethod "$env:SERVICE_ORDERS_API_ENDPOINT_URL/orders" | Out-Null
    Invoke-RestMethod "$env:SERVICE_ORDERS_API_ENDPOINT_URL/storage" | Out-Null
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

=== "Bash"

    ```bash
    cat ".workshop/${AZURE_ENV_NAME}/sre-agent-configuration.json"
    ```

=== "PowerShell"

    ```powershell
    Get-Content ".workshop/$env:AZURE_ENV_NAME/sre-agent-configuration.json"
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

=== "Bash"

    ```bash
    python scripts/workshop.py fault reset
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py fault reset
    ```

Do not grant write access merely to make the demonstration automatic.

## Cleanup problems

### `azd down --purge` stalls

Check resource-group state and locks:

=== "Bash"

    ```bash
    az group show --name "${RESOURCE_GROUP}" --query properties.provisioningState
    az lock list --resource-group "${RESOURCE_GROUP}" --output table
    ```

=== "PowerShell"

    ```powershell
    az group show --name $env:RESOURCE_GROUP --query properties.provisioningState
    az lock list --resource-group $env:RESOURCE_GROUP --output table
    ```

Review policy errors in the Activity Log. Delete only the confirmed workshop
scope; do not use broad or wildcard deletion commands.

<div class="sre-nav" markdown>
[:material-arrow-left: Workshop Variables](01-variables.md)
[Cost Management :material-arrow-right:](03-cost-management.md)
</div>
