---
title: Cost Management
description: Understand, monitor, reduce, and stop the costs of the single-VM Azure SRE Agent workshop.
ms.date: 2026-09-24
ms.topic: concept
keywords:
  - azure cost management
  - virtual machine costs
  - azure monitor costs
estimated_reading_time: 8
---

## Overview

The workshop is disposable but not free. Charges continue until resources are
deleted. Prices vary by subscription, region, currency, negotiated agreement,
and Azure SRE Agent offer, so use the Azure pricing calculator and Cost
Management rather than treating a workshop estimate as a quote.

## Cost drivers

| Resource | Cost characteristic |
| --- | --- |
| Ubuntu VM | Compute billed while allocated; default is `Standard_D2as_v5` |
| OS and data disks | Storage billed while disks exist, including while VM is deallocated |
| Standard public IP | Can incur hourly charges while retained |
| Log Analytics | Ingestion and retention; workshop daily cap is 1 GB |
| Application Insights | Workspace-based telemetry contributes to Log Analytics ingestion |
| Azure Monitor alerts | Scheduled-query alert rules can incur evaluation charges |
| Azure SRE Agent | Current agent and model pricing; always-on charges can apply |
| Network egress | Usually small for the workshop but not guaranteed to be zero |

The 8 GiB data-disk pressure exercise allocates a temporary local file; it does
not change the provisioned managed-disk size or storage tier.

## Free Account considerations

The initial Free Account credit can pay for eligible paid services during its
validity period. It does not make `Standard_D2as_v5` a monthly-free VM.
One-GiB monthly-free VM sizes are not validated for the on-VM .NET build plus
Azure Monitor Agent, and ARM64 sizes are incompatible with this x64 deployment.

Preflight reports quota and SKU constraints. It never upgrades a subscription,
removes a spending limit, requests quota, or silently substitutes a region or VM
size.

The 1 GB/day Log Analytics setting and 500 active-agent-unit SRE Agent setting are
service safeguards, not total monetary caps. Review current Azure documentation
before running the workshop in a constrained subscription.

## Monitor actual cost

In the Azure portal:

1. Open **Cost Management + Billing**.
2. Select **Cost analysis**.
3. Filter to the workshop subscription and resource group.
4. Group by **Resource type** or **Resource**.
5. Use a date range that includes the workshop.

Cost data can lag by 8 to 24 hours.

If your subscription exposes consumption data through the CLI:

```bash
source .workshop/workshop.env
az consumption usage list \
  --start-date "<yyyy-mm-dd>" \
  --end-date "<yyyy-mm-dd>" \
  --query "[?resourceGroup=='${RESOURCE_GROUP}'].{Resource:instanceName, Cost:pretaxCost, Currency:currency}" \
  --output table
```

Some sponsored, enterprise, or lab subscriptions do not expose this command to
the attendee.

## Reduce cost between sessions

The only complete cost stop is Module 07 deletion. For a short pause, deallocate
the VM:

```bash
source .workshop/workshop.env
az vm deallocate --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}"
```

Deallocation stops VM compute charges but does not remove disk, public-IP,
monitoring, alert, or SRE Agent costs. The public API and telemetry are
unavailable while the VM is stopped.

Restart and revalidate before continuing:

```bash
az vm start --resource-group "${RESOURCE_GROUP}" --name "${VM_NAME}"
python scripts/workshop.py smoke
python scripts/workshop.py inspect
python scripts/workshop.py telemetry
```

Do not deallocate during a fault exercise or while waiting for its alert.

## Configure a budget

Create a subscription or resource-group budget before a class:

1. Open **Cost Management** > **Budgets**.
2. Choose the workshop subscription or resource-group scope.
3. Set a realistic amount and end date.
4. Add notifications below, at, and above the expected spend.

A budget notifies; it does not automatically stop or delete resources.

## Stop all workshop costs

```bash
source .workshop/workshop.env
python scripts/workshop.py fault reset
azd down --purge
```

Verify the group is gone:

```bash
az group exists --name "${RESOURCE_GROUP}"
```

The expected result is `false`. Charges incurred before deletion can appear in
Cost Management later because billing records are delayed.

<div class="sre-nav" markdown>
[:material-arrow-left: Troubleshooting](02-troubleshooting.md)
[Running Locally or in Codespaces :material-arrow-right:](04-local-and-codespaces.md)
</div>
