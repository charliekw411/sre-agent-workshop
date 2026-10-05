---
title: Cost Management
description: Understand, monitor, reduce, and stop the costs of the Orders VM, private PostgreSQL, monitoring, and Azure SRE Agent workshop.
ms.date: 2026-10-06
ms.topic: concept
keywords:
  - azure cost management
  - postgresql flexible server costs
  - virtual machine costs
  - azure monitor costs
estimated_reading_time: 9
---

## Overview

The workshop is disposable but not free. Charges continue until resources are
deleted. Pricing varies by subscription, region, currency, agreement, usage,
and Azure SRE Agent offer. Use the Azure pricing calculator and Cost Management
rather than treating a workshop estimate as a quote.

Resetting CPU or PostgreSQL connectivity only restores the normal service state.
It does not stop any resource or stop billing.

## Cost drivers

| Resource | Workshop configuration and cost characteristic |
| --- | --- |
| Ubuntu VM | `Standard_D2as_v5` by default; compute is billed while allocated |
| VM supporting resources | The VM's required compute storage, NIC, and public endpoint remain until deletion |
| PostgreSQL Flexible Server | PostgreSQL 16, `Standard_B1ms`, Burstable compute |
| PostgreSQL storage | 32 GiB provisioned with auto-grow enabled |
| PostgreSQL backup | Seven-day locally redundant backup retention; usage and policy determine billed backup consumption |
| Private networking | VNet, delegated subnet, private DNS zone, and link; review current service pricing |
| Standard public IP | Can incur charges while retained |
| Log Analytics | Ingestion and retention; workshop daily cap is 1 GB |
| Application Insights | Workspace-based request, dependency, exception, and availability telemetry |
| PostgreSQL diagnostics | Server logs and metrics sent to the workspace contribute to ingestion |
| Azure Monitor alerts | Metric and scheduled-query rule evaluation can incur charges |
| Azure SRE Agent | Current agent and model pricing; always-on charges can apply |
| Network transfer | Normally small for the workshop, but not guaranteed to be zero |

The PostgreSQL incident changes one NSG rule between `Allow` and `Deny`. It does
not resize, stop, or delete the server, so its cost continues during and after
the exercise.

## Architecture and cost tradeoff

Running PostgreSQL on the Orders VM could reduce resource count, but it would
couple application and database lifecycle and remove the private dependency
boundary taught in Module 04. A public managed database could avoid private DNS
configuration but would expose a public endpoint.

Private Flexible Server was selected for repeatable provisioning, managed
database lifecycle, private networking, Entra authentication, and a realistic
dependency incident. To keep the lab affordable, it uses `Standard_B1ms`, no
HA, one application VM, one region, and seven-day locally redundant backups.
Those choices reduce cost and resilience together. They are not production
recommendations.

## Free Account considerations

Initial Free Account credit can pay for eligible services while valid. It does
not make the default application VM or PostgreSQL Flexible Server permanently
free.

Preflight reports VM quota and regional restrictions and checks that PostgreSQL
16 with `Standard_B1ms` is advertised. It never:

* Upgrades a subscription.
* Removes a spending limit.
* Requests quota.
* Changes region.
* Substitutes another VM or PostgreSQL SKU.

The Log Analytics 1 GB/day setting and any SRE Agent usage limit are safeguards,
not complete monetary caps. Ingestion can be delayed, and service-specific
charges can continue after a cap affects data or active usage.

## Monitor actual cost

In the Azure portal:

1. Open **Cost Management + Billing**.
2. Select **Cost analysis**.
3. Filter to the workshop subscription and resource group.
4. Group by **Resource type**, then by **Resource**.
5. Use a range that includes deployment and cleanup.

Look separately for Compute, PostgreSQL Flexible Server, Azure Monitor, public
IP, and Azure SRE Agent charges. Cost records can lag by 8 to 24 hours and can
appear after resource deletion.

If the subscription exposes consumption records:

=== "Bash"

    ```bash
    source .workshop/workshop.env
    az consumption usage list \
      --start-date "<yyyy-mm-dd>" \
      --end-date "<yyyy-mm-dd>" \
      --query "[?resourceGroup=='${RESOURCE_GROUP}'].{Resource:instanceName,Cost:pretaxCost,Currency:currency}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    az consumption usage list `
      --start-date "<yyyy-mm-dd>" `
      --end-date "<yyyy-mm-dd>" `
      --query "[?resourceGroup=='$env:RESOURCE_GROUP'].{Resource:instanceName,Cost:pretaxCost,Currency:currency}" `
      --output table
    ```

Sponsored, enterprise, and classroom subscriptions can expose different billing
views or withhold this command from participants.

## Pause the application VM

For a short break, deallocate the VM:

=== "Bash"

    ```bash
    az vm deallocate \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}"
    ```

=== "PowerShell"

    ```powershell
    az vm deallocate `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME
    ```

Deallocation stops application VM compute charges. It does not remove or stop:

* PostgreSQL Flexible Server compute, storage, or backup retention.
* Private DNS or network resources.
* The public IP and VM supporting resources.
* Monitoring, scheduled-query alerts, or retained telemetry.
* Azure SRE Agent.

The public API and new application telemetry are unavailable while the VM is
deallocated.

Start and revalidate before continuing:

=== "Bash"

    ```bash
    az vm start \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}"
    python scripts/workshop.py fault reset
    python scripts/workshop.py smoke
    python scripts/workshop.py inspect
    python scripts/workshop.py telemetry
    ```

=== "PowerShell"

    ```powershell
    az vm start `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME
    python scripts/workshop.py fault reset
    python scripts/workshop.py smoke
    python scripts/workshop.py inspect
    python scripts/workshop.py telemetry
    ```

Do not deallocate during an incident or while waiting for alert evidence.

## Configure a budget

Before a class:

1. Open **Cost Management** > **Budgets**.
2. Select the subscription or workshop resource-group scope.
3. Choose an amount and end date based on current regional prices.
4. Add notifications below, at, and above expected spend.
5. Include the organizer or subscription owner as a recipient.

A budget sends notifications. It does not automatically reset incidents, stop
resources, or delete the environment.

## Stop all workshop costs

First restore the normal state so final evidence and deletion begin from a known
configuration:

=== "Bash"

    ```bash
    source .workshop/workshop.env
    python scripts/workshop.py fault reset
    python scripts/workshop.py fault status
    azd down --purge --force
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    python scripts/workshop.py fault reset
    python scripts/workshop.py fault status
    azd down --purge --force
    ```

Cleanup removes the resource group and its:

* VM, public endpoint, and supporting resources.
* VNet, subnets, NSG, private DNS zone, and link.
* PostgreSQL server, database, and retained backups.
* Log Analytics, Application Insights, diagnostics, and alerts.
* SRE Agent, connectors, and identities.

Verify removal:

=== "Bash"

    ```bash
    az group exists --name "${RESOURCE_GROUP}"
    ```

=== "PowerShell"

    ```powershell
    az group exists --name $env:RESOURCE_GROUP
    ```

The expected result is `false`. Billing entries already incurred can arrive
later.

<div class="sre-nav" markdown>
[:material-arrow-left: Troubleshooting](02-troubleshooting.md)
[Running Locally or in Codespaces :material-arrow-right:](04-local-and-codespaces.md)
</div>
