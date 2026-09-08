---
title: Contoso Order Services architecture context
description: Architectural context supplied to Azure SRE Agent so investigations account for design constraints that are not visible in resource configuration.
ms.date: 2026-09-08
ms.topic: reference
---

## System overview

Contoso Order Services is a two-service order-processing platform running on Azure Container Apps with an Azure SQL Database back end. It accepts customer orders, resolves product pricing, and persists orders durably.

## Services

### orders-api

* Public HTTP API and the only externally reachable component.
* Accepts order submissions on `POST /orders` and serves order history on `GET /orders`.
* Calls `catalog-api` synchronously to resolve product pricing before every write.
* Writes orders to Azure SQL Database.
* Allocated 0.5 vCPU and 1 GiB memory.
* Fixed at exactly one replica. This is a deliberate workshop constraint and a known single point of failure.

### catalog-api

* Internal-only service with no external ingress.
* Returns product and pricing data on `GET /catalog/{productId}`.
* Allocated 0.25 vCPU and 0.5 GiB memory.
* Fixed at exactly one replica.
* Reachable only through `orders-api`, so its failures always surface as `orders-api` symptoms.

### Orders database

* Azure SQL Database, Standard S0 service objective.
* Maximum size is capped at 1 GB, which is far below the tier maximum. This is deliberate.
* Reaching the size cap fails writes with SQL error 40544 while reads continue to succeed.

## Business impact model

Not all operations carry equal weight. Rank impact accordingly.

| Operation         | Business function          | Impact when failing                        |
|-------------------|----------------------------|--------------------------------------------|
| `POST /orders`    | Order intake, revenue path | Severity 1. Direct revenue loss.           |
| `GET /orders`     | Order history browsing     | Severity 3. Degraded experience only.      |
| `GET /catalog`    | Internal pricing lookup    | Severity depends on effect on `POST /orders`. |

Always segment impact by operation. A service-wide failure rate that mixes reads and writes understates a total loss of order intake.

## Known architectural gaps

These are real properties of the system. Treat them as candidate contributing factors in any relevant incident.

* `orders-api` has no circuit breaker on its calls to `catalog-api`. Dependency failures pass through to customers as HTTP 500 at full request rate.
* `orders-api` has no fallback pricing path. There is no cache, no last-known price, and no deferred pricing mode.
* Retries have no exponential backoff and no jitter.
* Readiness and liveness probes check process health only. Neither reflects dependency health, so an unhealthy replica is never removed from rotation.
* Both services run a single replica, so there is no redundancy within a service.
* There is no telemetry for circuit breaker state, connection pool utilization, or storage growth rate.

## Deployment model

* Infrastructure is deployed with Bicep from `infra/main.bicep`, `infra/apps.bicep`, and `infra/alerts.bicep`.
* Container images are built with `az acr build` and pulled using a user-assigned managed identity.
* Changes appear in the Azure Activity log and as new Container Apps revisions. Check both when correlating an incident with a change.

## Fault injection

`orders-api` exposes workshop-only endpoints under `/fault`, protected by an `X-Fault-Token` header. When these are active, the container console log contains a line beginning `FAULT INJECTED`. Always search `ContainerAppConsoleLogs_CL` for that string early in an investigation, because it explains behavior that no configuration or deployment change would account for.

| Endpoint              | Injected condition                                    |
|-----------------------|-------------------------------------------------------|
| `POST /fault/cpu`     | CPU-bound threads saturate the `orders-api` allocation |
| `POST /fault/errors`  | `catalog-api` pricing lookups return HTTP 503          |
| `POST /fault/storage` | The orders database is filled toward its size cap      |
