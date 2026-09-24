---
title: Module 07 - Preserve Evidence and Clean Up
description: Capture final portal evidence, stop all workshop activity, preserve investigation artifacts, delete the Azure environment, and verify removal.
ms.date: 2026-09-24
ms.topic: how-to
keywords:
  - cleanup
  - azure developer cli
  - cost management
  - incident evidence
estimated_reading_time: 12
---

<ul class="sre-meta">
<li class="duration">Estimated time: 20 minutes</li>
<li>Module 07</li>
<li>Hands-on</li>
</ul>

## Overview

The workshop VM, managed disks, public IP, monitoring resources, and Azure SRE
Agent continue to incur charges until deleted. This module captures the final
visual evidence, stops every synthetic activity, preserves your notes, removes
the Azure environment, and verifies that the resource group is gone.

Deleting the resource group permanently removes the SQLite database and its
managed disk. Preserve only synthetic evidence appropriate for your
organization's data-handling policy.

## Learning objectives

* Capture a final healthy CPU and API view after both incidents.
* Stop load generation and reset bounded faults.
* Preserve notes, charts, alert history, and agent findings.
* Review the exact Azure scope before deletion.
* Delete and verify the workshop environment.

## Tasks

### Task 1: Capture the final healthy visual state

=== "Bash"

    ```bash
    source .workshop/workshop.env
    python scripts/workshop.py fault reset
    python scripts/workshop.py smoke
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    python scripts/workshop.py fault reset
    python scripts/workshop.py smoke
    ```

Send one final minute of healthy traffic:

=== "Bash"

    ```bash
    for i in $(seq 1 60); do
      curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/orders" > /dev/null
      sleep 1
    done
    ```

=== "PowerShell"

    ```powershell
    1..60 | ForEach-Object {
      Invoke-RestMethod "$env:SERVICE_ORDERS_API_ENDPOINT_URL/orders" |
        Out-Null
      Start-Sleep -Seconds 1
    }
    ```

Before deleting anything:

1. Open the VM **Monitoring** > **Metrics** blade.
2. Display **Percentage CPU** over the full workshop window.
3. Confirm the current segment is healthy and the earlier CPU spike remains
   visible.
4. Open Application Insights **Performance** and confirm current endpoint
   traffic.
5. Open **Monitor** > **Alerts**, include resolved alerts, and capture the CPU
   and data-disk alert history.
6. Save the final SRE Agent investigation summaries.

<!-- SCREENSHOT: Final VM CPU chart showing baseline, incident spike, and recovered state -->

This is the final required portal checkpoint. After deletion, these resource
blades and their retained telemetry are no longer available.

### Task 2: Stop workshop activity

Stop `generate-load.sh` with ++ctrl+c++ in any terminal where it is running.
Then verify the guest fault state:

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

Both `cpu` and `disk` should report inactive and `ballastBytes` should be zero.
The reset is safe to repeat and does not delete SQLite data.

### Task 3: Preserve the evidence worth keeping

Create a directory outside `.workshop` and copy your notes:

=== "Bash"

    ```bash
    mkdir -p "${HOME}/sre-workshop-artifacts"
    cp -R .workshop/notes/. "${HOME}/sre-workshop-artifacts/" 2>/dev/null || true
    find "${HOME}/sre-workshop-artifacts" -maxdepth 1 -type f -print
    ```

=== "PowerShell"

    ```powershell
    $destination = Join-Path $HOME 'sre-workshop-artifacts'
    New-Item -ItemType Directory -Force $destination | Out-Null
    if (Test-Path .workshop/notes) {
      Copy-Item .workshop/notes/* $destination -Recurse -Force
    }
    Get-ChildItem $destination
    ```

Keep:

* Baseline values and portal screenshots.
* CPU and data-disk timelines.
* Alert and response-plan timestamps.
* Agent drafts and your corrections.
* The final incident review and improvement backlog.

The JSON files under `.workshop/<environment>/` are deployment and validation
evidence. Copy them only if they are useful and permitted by your organization.

### Task 4: Review the deletion scope

=== "Bash"

    ```bash
    echo "Environment:    ${AZURE_ENV_NAME}"
    echo "Resource group: ${RESOURCE_GROUP}"
    echo "Subscription:   ${SUBSCRIPTION_ID}"

    az resource list \
      --resource-group "${RESOURCE_GROUP}" \
      --query "[].{Name:name, Type:type}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    "Environment:    $env:AZURE_ENV_NAME"
    "Resource group: $env:RESOURCE_GROUP"
    "Subscription:   $env:SUBSCRIPTION_ID"

    az resource list `
      --resource-group $env:RESOURCE_GROUP `
      --query "[].{Name:name, Type:type}" `
      --output table
    ```

Expect resources for:

* The Ubuntu VM, OS disk, managed data disk, NIC, public IP, VNet, and NSG.
* Log Analytics, Application Insights, Azure Monitor Agent, and the data
  collection rule.
* CPU, data-disk, and HTTP 5xx alert rules.
* Azure SRE Agent, its connectors, and managed identity.

Stop if the selected resource group is not the workshop group you intend to
delete.

### Task 5: Delete the environment

=== "Bash"

    ```bash
    azd down --purge
    ```

=== "PowerShell"

    ```powershell
    azd down --purge
    ```

Read the interactive confirmation carefully. This removes the resource group,
VM, both disks, public endpoint, telemetry, alert history, response plan, and
SRE Agent. There is no database backup.

Allow Azure several minutes to finish deletion.

### Task 6: Verify removal

=== "Bash"

    ```bash
    az group exists --name "${RESOURCE_GROUP}"
    ```

=== "PowerShell"

    ```powershell
    az group exists --name $env:RESOURCE_GROUP
    ```

The expected result is `false`. Also check for any remaining resource that uses
the deterministic workshop suffix:

=== "Bash"

    ```bash
    az resource list \
      --query "[?contains(name, '${WORKSHOP_SUFFIX}')].{Name:name, Type:type, Group:resourceGroup}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    az resource list `
      --query "[?contains(name, '$env:WORKSHOP_SUFFIX')].{Name:name, Type:type, Group:resourceGroup}" `
      --output table
    ```

An empty table is expected. If deletion is still in progress, wait and repeat
the checks. Resource locks or policy deny assignments are common reasons for a
stalled deletion.

### Task 7: Handle local environment state

The local azd environment and generated exports contain no secrets, but they
refer to resources that no longer exist. Remove them if you do not intend to
redeploy:

=== "Bash"

    ```bash
    azd env remove "${AZURE_ENV_NAME}"
    ```

=== "PowerShell"

    ```powershell
    azd env remove $env:AZURE_ENV_NAME
    ```

You can also remove the generated top-level export files after preserving notes:

=== "Bash"

    ```bash
    rm -f .workshop/workshop.env .workshop/workshop.ps1
    ```

=== "PowerShell"

    ```powershell
    Remove-Item .workshop/workshop.env,.workshop/workshop.ps1 -ErrorAction SilentlyContinue
    ```

Do not delete the repository or your copied review artifacts.

## Validation

* [x] You captured the final VM CPU, API, alert, and SRE Agent views.
* [x] All load generation and faults were stopped.
* [x] Notes and screenshots were copied outside deployment state.
* [x] You reviewed the resource inventory before deletion.
* [x] `azd down --purge` completed.
* [x] The resource group no longer exists and no suffix-matched resources remain.

## Knowledge check

??? question "Why capture portal charts before cleanup?"
    Deleting the resource group removes the monitored resources and their convenient portal context. A timestamped chart that shows baseline, incident, and recovery is part of the incident evidence and cannot be reconstructed reliably after telemetry is deleted.

??? question "Why reset faults before deleting the VM?"
    It verifies that the control path still works, avoids leaving synthetic work active during a delayed deletion, and separates a clean operational shutdown from infrastructure removal.

??? question "Why verify by suffix after the resource group is gone?"
    The resource-group check proves the intended scope was deleted. The suffix search catches an unexpected workshop resource created outside that scope or left behind by a failed deployment path.

## Workshop complete

You deployed and validated a persistent single-VM workload, established a visual
baseline, operated an SRE Agent response plan, handled CPU and disk-capacity
incidents, verified automated findings, and converted the evidence into an
improvement backlog.

[Return to workshop home :material-home:](../../index.md){ .md-button .md-button--primary }
[Troubleshooting](../30-appendix/02-troubleshooting.md){ .md-button }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 06 - Review and Improve](../06-review-and-improve/index.md)
[About the authors :material-arrow-right:](../29-about-the-authors/index.md)
</div>
