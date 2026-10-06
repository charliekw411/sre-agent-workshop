---
title: Workshop Variables and Evidence
description: Reference for the PostgreSQL workshop's non-secret exports, supported commands, and local validation evidence.
ms.date: 2026-10-06
ms.topic: reference
keywords:
  - environment variables
  - azure developer cli
  - postgresql
  - workshop evidence
estimated_reading_time: 8
---

## Overview

The selected Azure Developer CLI environment stores deployment inputs and
outputs. After `azd up`, the workshop writes an explicit non-secret allowlist:

* `.workshop/workshop.env` for Bash
* `.workshop/workshop.ps1` for PowerShell

These files contain resource names, IDs, private and public DNS names, and the
Orders API URL. They do not contain a PostgreSQL password, Microsoft Entra
access token, VM private key, API credential, or SRE Agent token.

## Select and load an environment

=== "Bash"

    ```bash
    azd env select "<your-environment>"
    source .workshop/workshop.env
    ```

=== "PowerShell"

    ```powershell
    azd env select "<your-environment>"
    . ./.workshop/workshop.ps1
    ```

Regenerate the allowlisted files without provisioning:

```bash
python scripts/workshop.py export
```

The helper selects the subscription recorded by azd before issuing Azure CLI
operations.

## Core variables

| Variable | Purpose |
| --- | --- |
| `AZURE_ENV_NAME` | Selected azd environment |
| `AZURE_LOCATION`, `LOCATION` | Deployment region |
| `AZURE_SUBSCRIPTION_ID`, `SUBSCRIPTION_ID` | Azure subscription GUID |
| `TENANT_ID` | Microsoft Entra tenant GUID |
| `AZURE_RESOURCE_GROUP`, `RESOURCE_GROUP` | Workshop resource group |
| `WORKSHOP_SUFFIX` | Deterministic resource-name suffix |
| `VM_NAME`, `VM_RESOURCE_ID` | Ubuntu Orders VM |
| `PUBLIC_IP_ADDRESS`, `PUBLIC_IP_RESOURCE_ID` | Public endpoint resource |
| `ORDERS_API_FQDN` | Stable Azure public DNS hostname |
| `SERVICE_ORDERS_API_ENDPOINT_URL` | Public Orders GUI and API base URL on HTTP port 8080 |
| `VIRTUAL_NETWORK_NAME` | Workshop VNet |
| `NETWORK_SECURITY_GROUP_NAME`, `NETWORK_SECURITY_GROUP_RESOURCE_ID` | Orders NSG |

## PostgreSQL variables

| Variable | Purpose |
| --- | --- |
| `POSTGRESQL_SERVER_NAME` | Flexible Server name, `psql-orders-<suffix>` |
| `POSTGRESQL_SERVER_RESOURCE_ID` | Flexible Server ARM resource ID |
| `POSTGRESQL_HOST` | Private PostgreSQL FQDN |
| `POSTGRESQL_DATABASE` | Application database name, `orders` |
| `POSTGRESQL_USER` | Microsoft Entra principal name used by the VM identity |
| `POSTGRESQL_SUBNET_RESOURCE_ID` | Delegated `10.240.0.32/27` subnet |
| `POSTGRESQL_PRIVATE_DNS_ZONE_NAME` | `private.postgres.database.azure.com` |
| `POSTGRESQL_PRIVATE_DNS_ZONE_RESOURCE_ID` | Private DNS zone ARM ID |
| `POSTGRESQL_FAULT_RULE_NAME` | `PostgreSqlFaultInjection` |
| `POSTGRESQL_FAULT_RULE_RESOURCE_ID` | Exact NSG child rule used by Module 04 |

`POSTGRESQL_USER` is an identity name, not a password. Azure runtime
authentication uses the VM's system-assigned identity and a short-lived
Microsoft Entra token. The connection configuration requires TLS
`VerifyFull`.

## Monitoring and SRE Agent variables

| Variable | Purpose |
| --- | --- |
| `LOG_ANALYTICS_NAME`, `LOG_ANALYTICS_ID` | Log Analytics workspace |
| `LOG_ANALYTICS_CUSTOMER_ID` | Workspace GUID used by log-query APIs |
| `APP_INSIGHTS_NAME`, `APP_INSIGHTS_RESOURCE_ID` | Workspace-based Application Insights |
| `DATA_COLLECTION_RULE_ID` | Azure Monitor Agent performance rule |
| `SRE_AGENT_NAME`, `SRE_AGENT_RESOURCE_ID` | Azure SRE Agent resource |
| `SRE_AGENT_ENDPOINT` | Agent endpoint used by deployment configuration |
| `SRE_AGENT_PRINCIPAL_ID` | Agent system-assigned identity object ID |
| `SRE_AGENT_IDENTITY_RESOURCE_ID` | Operational user-assigned identity resource |
| `SRE_AGENT_IDENTITY_PRINCIPAL_ID` | Operational identity object ID |

PowerShell accesses the same values through `$env:VARIABLE_NAME`.

The private azd environment can contain deployment inputs that are not exported.
Do not replace the allowlist with a dump of every azd or process environment
value.

## Orders GUI and JSON API

Open `SERVICE_ORDERS_API_ENDPOINT_URL` in a normal browser navigation to receive
the Orders GUI. The root uses content negotiation, so command-line clients
without `Accept: text/html` receive the JSON service descriptor.

These routes always return JSON:

| Route | Purpose |
| --- | --- |
| `/orders` | List or create synthetic orders |
| `/orders/{orderId}` | Read one order |
| `/orders/{orderId}/quantity` | Update quantity |
| `/health/live` | Process liveness |
| `/health/ready` | PostgreSQL schema-backed readiness |
| `/database` | Provider, status, server version, schema version, and database size |

The public API has no `/fault/*` route. It is separate from the documentation
site and its authenticated Azure controls.

## Supported workshop commands

| Command | Purpose |
| --- | --- |
| `python scripts/workshop.py smoke` | Validate public orders, PostgreSQL status, and absent HTTP fault routes |
| `python scripts/workshop.py inspect` | Verify service, private DNS, TLS, managed identity, PostgreSQL 16, migration, seed, and health |
| `python scripts/workshop.py telemetry` | Wait for required VM, request, PostgreSQL dependency, and availability signals |
| `python scripts/workshop.py verify-restart` | Restart the VM and prove order persistence in the same PostgreSQL resource |
| `python scripts/workshop.py fault status` | Read CPU unit and PostgreSQL NSG rule, liveness, readiness, and order-operation state |
| `python scripts/workshop.py fault cpu [seconds] [workers]` | Start CPU pressure; defaults to 300 seconds and two workers |
| `python scripts/workshop.py fault postgresql` | Verify `Deny`, recycle only the API, and require controlled PostgreSQL 503s |
| `python scripts/workshop.py fault reset-cpu` | Stop only CPU pressure; do not change the PostgreSQL rule |
| `python scripts/workshop.py fault reset-postgresql` | Verify `Allow`, recycle only the API, and require PostgreSQL recovery; do not change CPU |
| `python scripts/workshop.py fault reset` | Explicitly reset and verify both scenarios for final cleanup |
| `python scripts/workshop.py export` | Rebuild generated shell exports |

The wrapper scripts call the same implementation:

=== "Bash"

    ```bash
    ./scripts/inject-fault.sh status
    ```

=== "PowerShell"

    ```powershell
    ./scripts/inject-fault.ps1 status
    ```

CPU uses authenticated VM Run Command. PostgreSQL fault and its specific reset first use an
authenticated resource-group deployment of `infra/fault.bicep` and read the
rule back. They then use Run Command to recycle only `orders-api` and verify
localhost health/order results, because NSGs do not terminate established
pooled connections. Scenario-specific resets do not mutate the other fault.
The unqualified `fault reset` is intentionally the all-scenarios cleanup.
Neither action calls a public HTTP control endpoint.

## Validation evidence

Commands write ignored JSON evidence under:

```text
.workshop/<environment>/
```

Common files include:

| File | Evidence |
| --- | --- |
| `deployment.json` | Deployment stage durations and outcomes |
| `vm-deployment.json` | Guest installation and inspection result |
| `sre-agent-configuration.json` | Response-plan read-back |
| `smoke.json` | Public order, database-status, and retired-route checks |
| `vm-inspection.json` | Service, private DNS, TLS, PostgreSQL version, migration, seed, and health checks |
| `persistence-witness.json` | Order and bound `postgresqlServerResourceId` |
| `restart.json` | Before and after boot IDs and PostgreSQL resource binding |
| `telemetry.json` | Required signal sample counts |
| `fault-cpu.json` | CPU fault start result |
| `fault-reset-cpu.json` | CPU-only reset and unchanged PostgreSQL rule state |
| `fault-postgresql.json` | Verified rule deny, API recycle, and controlled 503 result |
| `fault-postgresql-partial.json` | Rule reached `Deny`, but recycle/data-plane verification failed |
| `fault-status.json` | CPU, rule, and current application connectivity state |
| `fault-reset-postgresql.json` | PostgreSQL-only rule allow, API recycle, and application recovery |
| `fault-reset-postgresql-partial.json` | Rule reached `Allow`, but PostgreSQL-only recovery verification failed |
| `fault-reset.json` | Explicit all-scenarios CPU stop and verified PostgreSQL recovery |
| `fault-reset-partial.json` | Rule reached `Allow`, but recycle/data-plane recovery verification failed |
| `fresh-benchmark.json` | Fresh deployment duration and stage evidence |

These files can contain Azure resource IDs and operational timestamps. Handle
them according to your organization's policy. Put working notes under
`.workshop/notes/` and copy needed evidence elsewhere before cleanup.

## Authentication is not exported

Azure CLI, azd, managed identity, and SRE Agent use their normal token paths.
Tokens are not written to workshop exports.

Linux VM provisioning requires a public SSH key, but the generated private half
is discarded and inbound SSH is blocked. Administration uses VM Run Command.

## Incident timestamps

`INCIDENT_START` is a local shell value, not a deployment output. Record all
incident times in UTC. For PostgreSQL, keep the rule-change time, first failed
dependency, first HTTP 503, alert time, reset, first successful dependency, and
alert resolution as separate events.

<div class="sre-nav" markdown>
[:material-arrow-left: About the authors](../29-about-the-authors/index.md)
[Troubleshooting :material-arrow-right:](02-troubleshooting.md)
</div>
