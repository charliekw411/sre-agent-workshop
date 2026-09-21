---
title: Module 02 - Solution Architecture
description: The Contoso Order Services architecture, request flow, telemetry pipeline, fault injection design, and failure modes used throughout the workshop.
ms.date: 2026-09-21
ms.topic: concept
keywords:
  - solution architecture
  - azure container apps
  - application insights
  - telemetry pipeline
estimated_reading_time: 12
---

<ul class="sre-meta">
<li class="duration">Estimated time: 25 minutes</li>
<li>Module 02</li>
<li>Reading</li>
</ul>

## Overview

You cannot investigate a system you do not understand, and neither can an agent. This module walks the architecture you deploy in Module 03: what each component does, how requests flow, where telemetry goes, and which parts are designed to fail.

Read it properly. Deployment loads the checked-in architecture as agent knowledge. In Module 12 you improve that Markdown and synchronize it with `azd up` or `python scripts/workshop.py configure-agent`; the quality of that description directly affects the diagnoses you get.

## Learning objectives

* Describe each component of Contoso Order Services and its responsibility.
* Trace a request from ingress through to the database.
* Explain how telemetry reaches Log Analytics and Application Insights.
* Identify the three failure modes and the blast radius of each.
* Explain why the fault-injection endpoints are designed the way they are.

## Architecture context

This image shows the application and telemetry flows, not the complete network
design. The private data-service paths are shown separately below.

![Contoso Order Services solution architecture showing request, telemetry, alerting, and Azure SRE Agent flows](../../assets/images/solution-architecture.png)

### Private data-service connectivity

```mermaid
flowchart LR
    Client[Public HTTPS client] --> Orders
    subgraph VNet["vnet-&lt;suffix&gt;"]
        subgraph ACA["Delegated subnet: cae-private-&lt;suffix&gt;"]
            Orders[orders-api] --> Catalog[catalog-api: internal ingress]
            Jobs[SQL bootstrap and token/fault jobs]
        end
        SQLPE[SQL private endpoint]
        VaultPE[Key Vault private endpoint]
        Orders --> SQLPE
        Orders --> VaultPE
        Jobs --> SQLPE
        Jobs --> VaultPE
    end
    SQLPE --> SQL["Azure SQL: public access disabled"]
    VaultPE --> Vault["Key Vault: public access disabled"]
```

The Container Apps environment uses a Consumption workload profile in a subnet
delegated to `Microsoft.App/environments`. SQL and Key Vault each have
`publicNetworkAccess: Disabled` and an endpoint on the separate private-endpoint
subnet. Private DNS zones `privatelink.database.windows.net` and
`privatelink.vaultcore.azure.net` are linked to `vnet-<suffix>`, so their normal
service hostnames resolve to private endpoint addresses for these workloads.
There is no public SQL firewall or `AllowAllWindowsAzureIps` rule.

This is not an all-private environment. Orders retains public HTTPS ingress;
Catalog retains internal ingress. Basic ACR remains public for Entra-authenticated
remote builds and managed-identity image pulls, with admin and anonymous access
off. Azure Monitor ingestion and queries also remain public. No Premium ACR,
dedicated build pool, NAT gateway, or VPN is added. If policy also prohibits these
public paths, additional architecture work or an approved environment is required,
not a policy bypass.

## Component responsibilities

### orders-api

The public entry point. It accepts order submissions, queries order history, and calls `catalog-api` to resolve product details before persisting an order. It runs with external ingress on a single replica by default, which keeps the CPU incident in Module 06 reproducible; an autoscaled service would simply add replicas and hide the saturation.

It also hosts the fault-injection controller at `/fault/*`, gated by an `X-Fault-Token` header.

`PUT /orders/{id}/quantity` updates an existing order's quantity with values from
1 through 1000. This exercises the runtime's narrow SQL update permission without
granting schema-management rights.

### catalog-api

An internal-only service that returns product and pricing data. It has no ingress
from outside the environment, so external clients reach its functionality through
`orders-api`. That constraint matters in Module 08: when `catalog-api` degrades,
the customer-visible symptom appears on `orders-api`, and the investigation has to
walk the dependency chain backwards.

### Azure SQL Database

A Standard S0 database with a deliberately capped 1 GiB maximum size. The small
ceiling makes the storage exhaustion incident finish in minutes instead of hours.
Authentication is Entra-only: a separate SQL bootstrap job identity creates the
schema and grants; the Orders API identity has object-level permissions, not
`db_owner`. Initialization inserts the five deterministic seed orders documented
in [Module 03](../03-deploy-infrastructure/index.md) without overwriting existing
orders or storage ballast.

### Key Vault and private jobs

The vault holds `fault-token`. The manual-trigger `workshop-token-init` job uses
`id-token-<suffix>` with vault-scoped `Key Vault Secrets Officer`. During
`postprovision` it creates only a missing token and preserves an existing token.
The hook waits for success, attaches the private Key Vault reference to Orders,
and then enables fault endpoints. Initial app provisioning leaves faults disabled.
The previous `Microsoft.Resources/deploymentScripts` resource
`generate-fault-token` is removed; there is no script-supporting storage account
or Azure Container Instance.

The separate `workshop-fault-client` job uses `id-fault-<suffix>` with only
vault-scoped `Key Vault Secrets User`. It reads the credential inside the VNet and
calls the existing Orders `/fault` routes. It cannot create or rotate the token.
Neither job gives the SRE runtime permission to start jobs or retrieve secrets.

### Log Analytics workspace

The single destination for Container Apps console logs, Container Apps system logs, and Azure SQL Database diagnostics. Centralizing here is what lets a single KQL query correlate an application exception with a platform event.

### Application Insights

Workspace-based, backed by the same Log Analytics workspace. It captures the request, dependency, and exception telemetry that turns "the service is slow" into "the `GET /catalog/{id}` dependency call is timing out".

### Azure SRE Agent

Scoped to the resource group with read access to resources and telemetry.
`azd up` provisions it, assigns permissions, and synchronizes the checked-in
configuration. Module 05 verifies the deployment.

## Request flow

A successful order submission touches every component.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant O as orders-api
    participant K as catalog-api
    participant D as Azure SQL Database
    participant A as Application Insights

    C->>O: POST /orders
    O->>A: Request telemetry started
    O->>K: GET /catalog/{productId}
    K-->>O: 200 product and price
    O->>A: Dependency telemetry (HTTP)
    O->>D: INSERT INTO Orders
    D-->>O: Rows affected
    O->>A: Dependency telemetry (SQL)
    O-->>C: 201 Created with order id
    O->>A: Request telemetry completed
```

Each arrow to Application Insights is a correlated telemetry item sharing one operation ID. That correlation is what lets an agent reconstruct the sequence during an investigation rather than guessing at timestamps.

## Telemetry pipeline

| Source                   | Mechanism              | Destination            | Used by                       |
|--------------------------|------------------------|------------------------|-------------------------------|
| Container console output | Container Apps logging | `ContainerAppConsoleLogs_CL` | Log queries, log alert rules |
| Container platform events| Container Apps logging | `ContainerAppSystemLogs_CL`  | Restart and probe analysis   |
| Container Apps metrics   | Azure Monitor platform | Metric store           | CPU and memory metric alerts  |
| SQL diagnostics          | Diagnostic settings    | `AzureDiagnostics`     | Query and error analysis      |
| SQL metrics              | Azure Monitor platform | Metric store           | Storage percent metric alert  |
| App requests and traces  | OpenTelemetry / SDK    | Application Insights   | Dependency and exception analysis |

!!! note "Ingestion latency is real"
    Platform metrics surface in roughly one to three minutes. Log Analytics ingestion is typically two to five minutes. Log-based alert rules add their own evaluation window. When a module tells you to wait five minutes before checking, that number came from these delays, not from an abundance of caution.

## Fault injection design

The fault endpoints live in `orders-api` and follow three rules.

* Every endpoint requires a matching `X-Fault-Token` header. Only the in-VNet fault-client job retrieves the credential for helper requests; the attendee laptop never retrieves it. Generated shell exports contain only safe identifiers and endpoints.
* Every endpoint is bounded. CPU load stops after a duration, error injection has a decaying time-to-live, and storage fill has a row cap.
* Every endpoint has a reset. `POST /fault/reset` clears all active fault state so you can return the system to health without redeploying.

| Endpoint              | Effect                                                        | Module |
|-----------------------|---------------------------------------------------------------|--------|
| `POST /fault/cpu`     | Saturates worker threads with a busy loop for N seconds       | 06     |
| `POST /fault/errors`  | Makes `catalog-api` calls fail at a configured rate            | 08     |
| `POST /fault/storage` | Bulk inserts padded rows until the database hits its size cap | 10     |
| `POST /fault/storage/release` | Releases storage ballast through the scoped procedure | 10     |
| `POST /fault/reset`   | Clears all fault state                                        | All    |
| `GET  /fault/status`  | Returns currently active faults                                | All    |

`scripts/inject-fault.sh` and `scripts/inject-fault.ps1` keep the same commands and
defaults. They start and wait for the job through ARM, then use the Azure CLI
`log-analytics` extension to read only its correlated, non-secret JSON result from
`ContainerAppConsoleLogs_CL`. The caller needs job-start and log-query permission,
not VPN connectivity or private-vault data access.

Progress is written to stderr and the JSON result to stdout. Retrieval waits up
to five minutes for ingestion after job success. A status response is the snapshot
captured by the job, not necessarily the current state at log arrival; fault timers
can expire during that delay. If retrieval times out, the helper reports the
execution and 32-character request ID. Retry only
`python scripts/workshop.py fault-result <request-id>`, which queries the last hour
(`PT1H`) without starting another job. Do not reinject to recover a missing result.
Log availability policies apply; a failed job needs log investigation instead.

!!! danger "These endpoints are hostile by design"
    They exist to destroy the availability of the service that hosts them. Never merge this controller into a real application. If you adapt this workshop for internal training, keep the fault code behind a compile-time flag that is off in every configuration except the workshop.

## Failure modes

### Failure mode 1: CPU saturation

`orders-api` consumes all available CPU. Requests queue, latency climbs, and eventually the ingress times out. `catalog-api` and the database stay healthy, so the blast radius is a single service. This is the simplest incident and it teaches the baseline investigation loop.

### Failure mode 2: Dependency failure cascade

`catalog-api` calls begin failing. `orders-api` retries, retries amplify load, thread pool utilization rises, and the customer sees HTTP 500 from a service that is not itself broken. Blast radius is two services and the symptom appears on the wrong one, which is the entire point.

### Failure mode 3: Storage exhaustion

The database reaches its size ceiling. Reads keep working, writes fail, and the application returns errors only on the write path. Partial failure is harder to triage than total failure because dashboards showing average success rate look almost normal.

```mermaid
flowchart LR
    subgraph F1["Failure 1: CPU"]
        A1[orders-api saturated] --> A2[Latency rises] --> A3[Timeouts]
    end
    subgraph F2["Failure 2: Dependency"]
        B1[catalog-api errors] --> B2[orders-api retries] --> B3[HTTP 500 to client]
    end
    subgraph F3["Failure 3: Storage"]
        C1[Database at size cap] --> C2[Writes rejected] --> C3[Partial outage]
    end
```

## Design decisions worth questioning

A workshop that presents architecture as settled fact teaches the wrong habit. Three choices here are debatable.

* Single replica for `orders-api`. Correct for reproducibility, wrong for production. Discuss what would change in each investigation if the service ran five replicas behind a load balancer.
* Basic tier SQL Database. Correct for cost, wrong for a workload that accepts orders. Consider how a serverless tier with auto-pause would change the storage incident.
* No circuit breaker between `orders-api` and `catalog-api`. Deliberately omitted so that Module 08 has something to find. Note that this omission is the actual root cause of the cascade, not the `catalog-api` failure itself.

## Validation

* [x] You can name the two services and explain which one has external ingress.
* [x] You can trace a `POST /orders` request through all four hops.
* [x] You can state which failure mode produces symptoms on a service that is not the faulty one.
* [x] You can explain why the fault endpoints require a token.

## Expected results

You should be able to sketch the architecture from memory on a whiteboard, including the telemetry paths. If you cannot, re-read the diagrams before continuing, because Module 07 asks you to evaluate whether an agent's description of the architecture is accurate.

## Knowledge check

??? question "During the Module 08 incident, which service shows HTTP 500 responses, and which service is actually at fault?"
    `orders-api` returns HTTP 500 to the client because it is the only externally reachable service. The fault originates in the `orders-api` to `catalog-api` dependency call. The deeper root cause is architectural: `orders-api` has no circuit breaker, so a failing dependency becomes a customer-facing outage rather than a degraded experience.

??? question "Why is Application Insights configured as workspace-based rather than classic?"
    Workspace-based Application Insights writes into the same Log Analytics workspace as platform logs. That lets a single KQL query join application exceptions against container restarts and SQL diagnostics. Classic Application Insights keeps telemetry in a separate store, forcing cross-resource queries and complicating correlation for both humans and the agent.

??? question "Storage exhaustion causes writes to fail while reads succeed. Why is that harder to triage than a total outage?"
    Aggregate health metrics stay in an acceptable range because the read path dominates request volume. Availability dashboards look mostly green, error rate rises but does not spike, and the failure is only obvious if you segment by operation. Partial failures need dimensional analysis, not averages.

## Next steps

You know what you are building and why. Time to deploy it.

[Next: Module 03 - Deploy Azure Infrastructure :material-arrow-right:](../03-deploy-infrastructure/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 01 - Prerequisites](../01-prerequisites/index.md)
[Module 03 - Deploy Azure Infrastructure :material-arrow-right:](../03-deploy-infrastructure/index.md)
</div>
