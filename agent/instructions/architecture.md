---
title: Contoso Order Services architecture context
description: Architectural context supplied to Azure SRE Agent so investigations account for design constraints that are not visible in resource configuration.
ms.date: 2026-09-21
ms.topic: reference
---

## System overview

Contoso Order Services is a two-service order-processing platform running on Azure Container Apps with an Azure SQL Database back end. It accepts customer orders, resolves product pricing, and persists orders durably.

## Services

### orders-api

* Public HTTPS API and the only externally reachable application service.
* Accepts order submissions on `POST /orders` and serves order history on `GET /orders`.
* Updates an existing order through `PUT /orders/{id}/quantity`, accepting quantities from 1 through 1000.
* Calls `catalog-api` synchronously to resolve product pricing before every write.
* Writes orders to Azure SQL Database.
* Allocated 0.5 vCPU and 1 GiB memory.
* Fixed at exactly one replica. This is a deliberate workshop constraint and a known single point of failure.

### catalog-api

* Internal-only service with no external ingress.
* Returns product and pricing data on `GET /catalog/{productId}`.
* Allocated 0.25 vCPU and 0.5 GiB memory.
* Fixed at exactly one replica.
* External clients reach Catalog functionality through `orders-api`, so its failures surface there as customer-visible symptoms.

### Orders database

* Azure SQL Database, Standard S0 service objective.
* Maximum size is capped at 1 GB, which is far below the tier maximum. This is deliberate.
* Reaching the size cap fails writes with SQL error 40544 while reads continue to succeed.
* Authentication is Microsoft Entra-only. A separate bootstrap job identity is the SQL administrator; the Orders API identity has object-level grants, not `db_owner`.
* Storage release uses a narrowly scoped privileged stored procedure, rather than granting schema ownership to the runtime.
* Initialization inserts only missing seed orders, preserving existing orders and storage ballast on redeployment. Reserved IDs -1 through -5 map to SKU-1001 through SKU-1005, priced 129.99, 349.00, 219.50, 45.75, and 189.00. All use customer `workshop-seed`, quantity 1, and timestamp `2026-01-01T00:00:00Z`.

## Networking and governance

* The Container Apps environment is `cae-private-<suffix>`, using a Consumption workload profile and a subnet delegated to `Microsoft.App/environments` in `vnet-<suffix>`.
* SQL and Key Vault both have `publicNetworkAccess: Disabled`. Each has a private endpoint on the separate private-endpoint subnet: `pe-sql-<suffix>` and `pe-vault-<suffix>`.
* Private DNS zones `privatelink.database.windows.net` and `privatelink.vaultcore.azure.net` are linked to the VNet. The normal SQL and vault hostnames resolve to private endpoint addresses from the apps and jobs.
* The public SQL firewall and `AllowAllWindowsAzureIps` rule have been removed. Their absence is intentional, not a missing configuration to repair.
* Orders HTTPS ingress remains public and Catalog ingress remains internal. Basic ACR remains public with Entra-authenticated remote builds, managed-identity image pulls, and admin/anonymous access off. Azure Monitor ingestion and queries remain public. This is not an all-private design.
* There is no Premium ACR, dedicated build pool, NAT gateway, or VPN in this scope.

For SQL or vault connectivity failures, investigate private endpoint approval,
private DNS records and VNet links, the environment's delegated subnet, app/job
environment references, and managed-identity permissions. Check SQL bootstrap
success and the Orders contained user/object grants separately from networking.
An attendee laptop without VNet access cannot test private data-plane connectivity.
Use read-only resource configuration and logs; request an authorized operator's
in-network diagnostic evidence if needed, not new privileges for the SRE runtime.

Inherited policies can disable SQL and vault public access. In the former
deployment, `DenyPublicEndpointEnabled` rejected the SQL firewall rule because
SQL public access had been disabled. The former deployment script's supporting
storage failed with `KeyBasedAuthenticationNotPermitted` when storage shared
keys were prohibited.
The private endpoints and storage-free managed-identity jobs address these
constraints. Never propose re-enabling public SQL/vault access, recreating a
public SQL firewall exception, using SQL passwords or storage shared keys, adding
policy-bypass tags, or obtaining exemptions as an incident workaround. If policy
also blocks public ACR, Azure Monitor, Orders ingress, or required preview
resources, additional architecture work or an approved environment is required.

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

* `azd up` runs `provision`, then `package`, then `deploy --all`. Bicep provisions networking, apps, monitoring, SRE Agent, Key Vault, and role assignments.
* Initial app provisioning keeps fault endpoints disabled. `postprovision` starts `workshop-token-init`, waits for success, attaches the private Key Vault reference to Orders, then enables faults.
* `workshop-token-init` uses `id-token-<suffix>` with vault-scoped `Key Vault Secrets Officer`. It creates only a missing `fault-token`, preserving an existing token on redeployment.
* The old ARM `Microsoft.Resources/deploymentScripts` resource `generate-fault-token` is removed. No script-supporting storage account or Azure Container Instance is needed; `Microsoft.Network` replaces `Microsoft.ContainerInstance` and `Microsoft.Storage` in required provider registration.
* Application images are built remotely in public Basic ACR using Entra authentication and pulled using managed identity. Attendees need no local Docker daemon or .NET SDK.
* `postdeploy` starts the manual-trigger SQL initialization job, waits for success, makes a smoke request, then synchronizes `agent/incident-filters.yaml` and `agent/knowledge.yaml` and waits for indexing.
* Agent instructions and runbooks are checked-in Markdown. Refresh them through `azd up` or `python scripts/workshop.py configure-agent`, not portal configuration.
* The SRE runtime has Reader and Monitoring Reader at resource-group scope and Log Analytics Reader at workspace scope. It investigates read-only and cannot start jobs, remediate, access fault secrets, or acknowledge/close Azure Monitor alerts.
* The attendee's agent-scoped SRE Agent Administrator role permits configuration; it does not expand runtime permissions.
* Changes appear in the Azure Activity log and as new Container Apps revisions. Check both when correlating an incident with a change.

Existing apps and jobs on the old non-VNet `cae-<suffix>` environment cannot move in place.
Preprovision stops before attempting a move or deletion and requires a new azd
environment name. If an earlier failed deployment created no apps or jobs, the same azd
environment can be retried, creating `cae-private-<suffix>` and reusing SQL, vault,
and data. Old empty environments and failed deployment-script metadata are not
automatically deleted; they remain until deliberate resource-group cleanup.
Never suggest deleting apps or data to force an automatic migration.

## Fault injection

`orders-api` exposes workshop-only endpoints under `/fault`, protected by an `X-Fault-Token` header. When these are active, the container console log contains a line beginning `FAULT INJECTED`. Always search `ContainerAppConsoleLogs_CL` for that string early in an investigation, because it explains behavior that no configuration or deployment change would account for.

Attendees invoke the Bash or PowerShell fault helper with the existing commands
and defaults. The helper starts and waits for `workshop-fault-client` through ARM.
That job uses `id-fault-<suffix>` with only vault-scoped `Key Vault Secrets User`,
retrieves the credential inside the VNet, and calls the existing `/fault` routes.
The laptop never retrieves the credential and needs no VPN or private-vault
data access. The caller needs job-start/read and workspace-query permission,
included in the required subscription Owner or Contributor plus User Access
Administrator roles. The attendee's legacy vault Secrets User grant remains for
authorized in-network administration and is not used by local helpers.

The helper uses the Azure CLI `log-analytics` extension to retrieve only the
correlated non-secret JSON result from `ContainerAppConsoleLogs_CL`, marked
`WORKSHOP_RESULT:<32-character-request-id>:<JSON>`. Progress goes to stderr and
JSON to stdout. Generated shell files contain only allowlisted non-secret
identifiers and endpoints, including the job and private-network resource names.
No secrets are printed in results or errors.

Retrieval waits up to five minutes for log ingestion after job success. A status
response is a snapshot from the job and may not describe current state at log
arrival; fault timers continue during this delay. Correlate execution and telemetry
times rather than assuming the helper's return time is the injection time.

If logs are delayed, the helper reports the execution and 32-character request ID.
`python scripts/workshop.py fault-result <request-id>` retries only read-only log
retrieval for the last hour (`PT1H`), never another job or `/fault` request. Log
availability policies apply. Never recommend reinjection to recover a missing
result. A failed job requires inspection of its execution logs, not secret
retrieval or broader roles. The SRE runtime may read those non-secret logs but
cannot run either private job.

| Endpoint              | Injected condition                                    |
|-----------------------|-------------------------------------------------------|
| `POST /fault/cpu`     | CPU-bound threads saturate the `orders-api` allocation |
| `POST /fault/errors`  | `catalog-api` pricing lookups return HTTP 503          |
| `POST /fault/storage` | The orders database is filled toward its size cap      |
| `POST /fault/storage/release` | Releases storage ballast through the scoped procedure |
| `POST /fault/reset` | Resets active fault state |
| `GET /fault/status` | Captures a fault-state snapshot |
