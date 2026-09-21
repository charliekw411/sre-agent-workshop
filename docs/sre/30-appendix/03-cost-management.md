---
title: Cost Management
description: Estimated cost of the Azure SRE Agent workshop environment, the main cost drivers, and how to pause the environment between sessions.
ms.date: 2026-09-21
ms.topic: how-to
keywords:
  - cost management
  - azure pricing
  - budget
estimated_reading_time: 6
---

## Overview

The workshop environment is deliberately small, but it is not free. Private
endpoint hours continue to accrue while you are away, and Log Analytics ingestion
from a load generator left running overnight can dominate the bill.

The original application estimates below are approximate planning figures, not
current regional quotes. The new private-endpoint figure uses an illustrative
rate, not a verified price for your region. Taxes are excluded. Use the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/) for
your region and agreement.

## Estimated cost

| Resource                       | Configuration                        | Approximate cost per day |
|--------------------------------|--------------------------------------|--------------------------|
| Azure SQL Database             | Standard S0, 1 GB max size           | 0.50 USD                 |
| Container Apps                 | Two apps, 0.75 vCPU total, always on | 0.80 USD                 |
| Log Analytics                  | Ingestion and 30-day retention       | 0.30 to 2.00 USD         |
| Application Insights           | Workspace-based, included above      | Included                 |
| Container registry             | Basic tier                           | 0.17 USD                 |
| Private Endpoints              | Two, one for SQL and one for Key Vault | About 0.48 USD for endpoint hours at an illustrative 0.01 USD/hour per endpoint, plus data processing |
| Private DNS                    | Two VNet-linked zones and DNS queries | Region- and usage-dependent |
| Container Apps jobs            | Short on-demand SQL bootstrap, token initialization, and fault-client executions | Execution- and duration-dependent |
| Key Vault                      | Standard tier, managed-identity secret operations | Usage-dependent |
| Azure Monitor alert rules      | Five metric, two log                 | 0.20 USD                 |
| Managed identity               | User-assigned                        | Free                     |
| Azure SRE Agent                | See current product pricing          | Varies                   |

The original 2 to 4 US dollars per day estimate excluded Azure SRE Agent and
predated private networking and the token/fault jobs. It is not a complete
estimate for this design. Budget above that baseline for two billable Private
Endpoints, data processing, private DNS, and short on-demand job executions;
regional rates and actual usage vary. The illustrative endpoint-hours subtotal
alone adds about 0.48 US dollars per full day, even if the apps scale to zero.
Check current SRE Agent pricing separately.

This scope retains public Basic ACR remote builds and public Azure Monitor
ingestion/query and Orders ingress. It adds no Premium ACR, dedicated build pool,
NAT gateway, or VPN. If policy also requires those public paths to become private,
the extra architecture and costs need separate planning.

!!! warning "Log ingestion is the variable that surprises people"
    The load generator in Module 04 sends five requests per second. Each produces request telemetry, dependency telemetry, and console log lines across two services. Left running for a week that is several gigabytes of ingestion. Stop the generator when you stop working.

## Reducing cost between sessions

If you are running the workshop across multiple sessions, scaling the apps down
can reduce compute charges without deleting data. It does not pause SQL, registry,
Private Endpoint, or private DNS charges. Jobs run on demand rather than
continuously, but starting them and ingesting their logs still adds usage.

```bash
source .workshop/workshop.env

# Stop the load generator first (Ctrl+C in its terminal), then scale the apps to zero.
az containerapp update --name orders-api  --resource-group "${RESOURCE_GROUP}" --min-replicas 0 --max-replicas 1 --output none
az containerapp update --name catalog-api --resource-group "${RESOURCE_GROUP}" --min-replicas 0 --max-replicas 1 --output none

echo "Container apps will scale to zero when idle."
```

Restore before your next session.

```bash
az containerapp update --name orders-api  --resource-group "${RESOURCE_GROUP}" --min-replicas 1 --max-replicas 1 --output none
az containerapp update --name catalog-api --resource-group "${RESOURCE_GROUP}" --min-replicas 1 --max-replicas 1 --output none
```

!!! important "Restore the replica settings before Module 06"
    A scale range of 0 to 1 changes the behavior of the CPU incident. Set both apps back to a fixed single replica before continuing, or the saturation signal will be intermittent and the investigation will not match the documentation.

Reduce Log Analytics retention if you are pausing for more than a few days.

```bash
az monitor log-analytics workspace update \
  --resource-group "${RESOURCE_GROUP}" \
  --workspace-name "${LOG_ANALYTICS_NAME}" \
  --retention-time 30 \
  --output none
```

Thirty days is the minimum billed retention, and it is already the default in this workshop.

## Setting a budget

Create a budget with an alert so a forgotten environment cannot quietly accumulate cost.

```bash
source .workshop/workshop.env

az consumption budget create \
  --budget-name "budget-sre-workshop-${WORKSHOP_SUFFIX}" \
  --amount 25 \
  --category Cost \
  --time-grain Monthly \
  --start-date "$(date -u +%Y-%m-01)" \
  --end-date "$(date -u -d '+3 months' +%Y-%m-01 2>/dev/null || date -u -v+3m +%Y-%m-01)" \
  --resource-group "${RESOURCE_GROUP}" \
  --output none 2>/dev/null || echo "Budget creation is not supported on this subscription type. Use Cost Management in the portal."
```

## Checking actual spend

```bash
az consumption usage list \
  --start-date "$(date -u -d '7 days ago' +%Y-%m-%d 2>/dev/null || date -u -v-7d +%Y-%m-%d)" \
  --end-date "$(date -u +%Y-%m-%d)" \
  --query "[?contains(instanceName, '${WORKSHOP_SUFFIX}')].{Resource:instanceName, Cost:pretaxCost, Currency:currency}" \
  --output table
```

Billing data lags by up to 24 hours. An empty result the same day is normal.

## The cheapest option

Finish the workshop, then run [Module 14](../14-cleanup/index.md). A deleted resource group costs nothing, and the artifacts worth keeping are text files.

<div class="sre-nav" markdown>
[:material-arrow-left: Troubleshooting](02-troubleshooting.md)
[Running Locally or in Codespaces :material-arrow-right:](04-local-and-codespaces.md)
</div>
