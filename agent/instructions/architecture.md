---
title: Contoso Order Services architecture context
description: Architecture and operational constraints for the VM and private PostgreSQL workshop.
ms.date: 2026-09-25
ms.topic: reference
---

## System overview

Contoso Order Services is a deliberately small workshop application. One Ubuntu
VM runs the public .NET 8 `orders-api` as a hardened, non-root `systemd` service.
The API stores orders in a separate Azure Database for PostgreSQL Flexible Server.
There is no Catalog service, Container Apps environment, container registry, Key
Vault, or Azure SQL resource in this deployment.

The VM is a single point of failure. PostgreSQL uses no high availability. These
constraints keep cost and workshop setup bounded; do not infer production
redundancy from the use of a managed database.

## Application behavior

The Orders API listens on public HTTP port 8080. SSH and fault endpoints are not
publicly reachable.

| Endpoint | Behavior |
| --- | --- |
| `GET /` | Browser application or API metadata; does not prove database health |
| `GET /orders` | Reads the 25 most recent orders from PostgreSQL |
| `GET /orders/{id}` | Reads one order from PostgreSQL |
| `POST /orders` | Validates and inserts an order; optional `Idempotency-Key` |
| `PUT /orders/{id}/quantity` | Updates an order in PostgreSQL |
| `GET /database` | Reports PostgreSQL provider, server version, schema version, and database size |
| `GET /health/live` | Proves only that the process is running |
| `GET /health/ready` | Probes PostgreSQL connectivity and required schema objects |

Product prices are an in-process sample catalog. Every order read or write uses
PostgreSQL. A PostgreSQL connectivity failure therefore affects all order
operations and readiness, while the static browser and liveness endpoint can
remain available.

The API uses a singleton pooled Npgsql data source with five-second connection
and command timeouts. Each repository operation emits an `AppDependencies` item
whose `DependencyType` is `PostgreSQL`. Failures also emit sanitized
`AppExceptions`; credentials, tokens, and raw provider error text are not
recorded. A background probe emits `AppAvailabilityResults` named
`orders-api-postgresql` once per minute.

## PostgreSQL

* Azure Database for PostgreSQL Flexible Server 16.
* `Standard_B1ms`, 32 GiB storage with auto-grow, no high availability.
* Seven-day locally redundant backups.
* Database name `orders`.
* Public network access and password authentication are disabled.
* The private DNS zone is `private.postgres.database.azure.com`.
* Migration state is recorded in `orders_schema_migrations`; the current schema
  version is 1.
* Seed order IDs -1 through -5 are deterministic. Positive IDs use a PostgreSQL
  identity sequence that initialization synchronizes after seeding.

Schema bootstrap runs before `systemd` starts the API. It uses a PostgreSQL
advisory lock and a serializable transaction so repeated deployment is
idempotent and concurrent initializers cannot partially apply the schema.

The VM system-assigned managed identity is both the PostgreSQL Microsoft Entra
administrator and the runtime principal. The API obtains short-lived tokens for
`https://ossrdbms-aad.database.windows.net` and requires TLS certificate and
hostname verification. Combining administrator and runtime privilege is a
disposable-lab simplification, not a production pattern. Production should use
separate bootstrap and least-privileged runtime principals.

## Network path

The VNet is `10.240.0.0/24`:

* The Orders VM uses subnet `orders` (`10.240.0.0/27`).
* PostgreSQL uses delegated subnet `postgresql` (`10.240.0.32/27`).
* The PostgreSQL hostname resolves privately from the VM.
* The network security group `nsg-orders-<suffix>` permits public inbound TCP
  8080 and denies other inbound traffic.
* Outbound rule `PostgreSqlFaultInjection`, priority 100, normally allows only
  TCP 5432 from the Orders subnet to the PostgreSQL subnet.

For database failures, distinguish four boundaries:

1. Private DNS must return the Flexible Server private address.
2. TCP 5432 must cross the scoped NSG rule.
3. TLS verification and the managed-identity token must succeed.
4. The database and migration version must be ready.

Do not recommend enabling public database access, adding a firewall exception,
disabling TLS verification, or creating a password as an incident workaround.
Those changes defeat intentional controls and do not identify the failed
boundary.

## Monitoring and alerts

The VM has Azure Monitor Agent and a Data Collection Rule for heartbeat and CPU
performance counters. Application Insights is workspace based.

| Alert | Severity | Condition |
| --- | --- | --- |
| `alert-orders-high-cpu` | Sev2 | VM average Percentage CPU exceeds 80% for five minutes |
| `alert-orders-postgresql-connectivity` | Sev1 | Any failed PostgreSQL dependency is observed in five minutes |
| `alert-orders-http-5xx` | Sev1 | More than 10 HTTP 5xx responses occur in five minutes |

The PostgreSQL dependency alert is the primary incident signal for Module 04.
Use `AppDependencies`, `AppAvailabilityResults`, `AppRequests`, and
`AppExceptions` to establish impact. Use VM metrics only for the CPU incident;
normal VM CPU does not prove PostgreSQL is healthy.

## SRE Agent authority

The SRE Agent runtime has read-only Azure resource, monitoring, and Log Analytics
access. It can investigate but cannot run VM commands, edit the NSG, restart
resources, change PostgreSQL configuration, or close alerts. Recommendations
must identify the authorized operator action and must not claim remediation was
performed.

The attendee has agent-scoped administration to operate the response plan. That
role does not increase the runtime's infrastructure privileges.

## Deployment and persistence

`azd up` provisions the VM, private PostgreSQL, monitoring, alerts, and SRE Agent,
then the post-provision hook publishes and bootstraps the API. Compatible
resource groups carry `workshop-architecture=single-vm-postgresql-v1`. Older
workshop environments are rejected rather than migrated or deleted implicitly.

Deployment never emits a PostgreSQL password. Generated environment files
contain only allowlisted resource identifiers and endpoints.

Restart validation creates or reuses a witness order, binds that evidence to the
PostgreSQL server resource ID, restarts only the VM, and proves the same order
still exists afterward. This tests separation of compute and data lifecycle; it
does not prove database high availability.

## Fault injection

There are two operator-controlled incidents:

* CPU: authenticated Azure VM Run Command starts a bounded transient `systemd`
  unit. The default CLI action is 300 seconds with two workers; the browser lab
  uses fixed workshop limits.
* PostgreSQL connectivity: an authenticated control-plane action changes only
  `PostgreSqlFaultInjection` from `Allow` to `Deny`. It does not stop or alter
  PostgreSQL and does not block unrelated outbound traffic. Because Azure NSGs
  preserve established flows, the authorized injector then restarts only
  `orders-api` through VM Run Command and requires liveness HTTP 200 plus
  controlled readiness/order HTTP 503 responses. This recycle drains Npgsql
  sessions and makes delivery deterministic; it is not the incident trigger.

`python scripts/workshop.py fault status` reads both fault states and probes
current application connectivity without restarting the API.
`fault reset-cpu` stops only the CPU unit. `fault reset-postgresql` restores the
PostgreSQL rule to `Allow`, then restarts only `orders-api` and requires
readiness and an order operation to succeed without changing CPU. The
unqualified `fault reset` is explicitly the all-scenarios cleanup. A normal
`azd up` also reconciles the rule to `Allow`. The PostgreSQL fault has no timer,
so operators must reset it. Neither inject nor PostgreSQL reset is successful
on NSG state alone.

The NSG rule update and expected API-only Run Command appear in the Azure
Activity log. The scoped rule change, followed by the deliberate pool drain, a
sharp rise in PostgreSQL dependency failures, and failed readiness is evidence
for the workshop trigger. Do not identify the recycle as root cause. Rule timing
alone is correlation; verify dependency failure after `Deny` and recovery after
`Allow` before concluding causation.

## Known gaps and trade-offs

* One VM and one no-HA Flexible Server provide no application or database
  redundancy.
* The public Orders endpoint uses HTTP for workshop simplicity.
* The API has no circuit breaker or offline read cache for PostgreSQL.
* A single managed identity has bootstrap and runtime database privilege.
* Dependency telemetry does not expose Npgsql pool utilization.
* The SRE runtime cannot perform private data-plane probes from inside the VNet.
* PostgreSQL auto-grow reduces immediate capacity risk but is not a substitute
  for growth forecasting, retention policy, or cost controls.

Classify these as contributing factors only when evidence connects them to the
incident. Do not present a deliberate workshop limitation as the trigger.
