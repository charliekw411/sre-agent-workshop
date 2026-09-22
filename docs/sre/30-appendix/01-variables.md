---
title: Workshop Variables
description: Reference for safe deployment exports, private fault-client jobs, and read-only retrieval of delayed results.
ms.date: 2026-09-21
ms.topic: reference
keywords:
  - environment variables
  - reference
estimated_reading_time: 5
---

## Overview

The named `azd` environment holds deployment state. After deployment, hooks
generate `.workshop/workshop.env` for Bash and `.workshop/workshop.ps1` for
PowerShell using an explicit allowlist. These files contain non-secret resource
identifiers, names, and endpoints, not credentials.

=== "Bash"

    ```bash
    azd env select "<your-alias>-workshop"
    source .workshop/workshop.env
    ```

=== "PowerShell"

    ```powershell
    azd env select "<your-alias>-workshop"
    . ./.workshop/workshop.ps1
    ```

## Variable reference

| Variable | Purpose |
| --- | --- |
| `AZURE_ENV_NAME` | Selected azd environment |
| `WORKSHOP_SUFFIX` | Deterministic resource-name suffix |
| `LOCATION` | Deployment region; default `eastus2` |
| `RESOURCE_GROUP` | Isolated workshop resource group |
| `SUBSCRIPTION_ID`, `TENANT_ID` | Azure subscription and Entra tenant identifiers |
| `ACR_NAME`, `ACR_LOGIN_SERVER` | Registry name and image hostname |
| `LOG_ANALYTICS_NAME`, `LOG_ANALYTICS_ID` | Workspace name and resource ID |
| `LOG_ANALYTICS_CUSTOMER_ID` | Workspace GUID for log queries |
| `APP_INSIGHTS_NAME` | Application Insights resource name |
| `SQL_SERVER_NAME`, `SQL_DATABASE_NAME` | SQL resource names, not credentials |
| `BOOTSTRAP_JOB_NAME` | Manual-trigger SQL initialization job, started by hooks |
| `TOKEN_INITIALIZER_JOB_NAME` | `workshop-token-init`, started and checked by `postprovision` |
| `FAULT_CLIENT_JOB_NAME` | `workshop-fault-client`, started and checked by fault helpers |
| `KEY_VAULT_NAME` | Private vault accessed by managed-identity workloads, not by the local helper |
| `KEY_VAULT_URI`, `FAULT_TOKEN_SECRET_URI` | Vault and secret reference URIs, never secret values |
| `CONTAINER_ENV_NAME` | VNet-integrated Container Apps environment, `cae-private-<suffix>` |
| `VIRTUAL_NETWORK_NAME` | `vnet-<suffix>`, containing delegated Container Apps and private-endpoint subnets |
| `SQL_PRIVATE_ENDPOINT_NAME` | SQL private endpoint, `pe-sql-<suffix>` |
| `KEY_VAULT_PRIVATE_ENDPOINT_NAME` | Key Vault private endpoint, `pe-vault-<suffix>` |
| `ORDERS_API_FQDN` | Public API hostname |
| `SRE_AGENT_NAME`, `SRE_AGENT_PRINCIPAL_ID` | Agent resource name and runtime identity object ID |
| `SRE_AGENT_RESOURCE_ID`, `SRE_AGENT_ENDPOINT` | Agent ARM resource ID and configuration API endpoint |

PowerShell reads the exports as `$env:RESOURCE_GROUP`, `$env:ORDERS_API_FQDN`,
and so on. Incident start timestamps are session notes recorded during Modules
06, 08, and 10 rather than infrastructure outputs. The new job and network outputs
are additive; existing output names are preserved.

The deployment parameters also use azd's built-in `AZURE_PRINCIPAL_ID` and
`AZURE_PRINCIPAL_TYPE` for attendee identity resolution. These are azd inputs,
not additional workshop shell exports or custom variables you must fill in.

## Rebuilding the files

Select the correct environment and run `python scripts/workshop.py export` to
regenerate the allowlisted outputs from existing deployment state. `azd up`
also regenerates them through the hooks. Do not reconstruct them by dumping
every azd or shell variable: that bypasses the non-secret allowlist.
Redeployment initializes only a missing fault token, inserts only missing SQL seed
IDs, and preserves existing tokens, orders, and ballast.

For environments created with older workshop versions, the hook removes legacy
SQL/fault credential values from the selected `.azure/` environment's `.env` and
cached `config.json` parameters as well as the generated workshop exports.
Do not restore those values from an old backup.

Keep local state out of source control:

```bash
git check-ignore -v .workshop/workshop.env .workshop/workshop.ps1
```

## Authentication is not an exported variable

SQL is Microsoft Entra-only. The bootstrap job uses a separate administrator
managed identity; the Orders API identity has only runtime object permissions.
There is no SQL administrator password to generate, recover, or rotate.

Fault credentials stay in private Key Vault and the in-VNet workloads that use
them. These wrappers call the common Python helper to start and wait for
`workshop-fault-client` through ARM:

```bash
./scripts/inject-fault.sh status
```

```powershell
./scripts/inject-fault.ps1 status
```

The job's `id-fault-client-<suffix>` identity has only vault-scoped `Key Vault Secrets User`.
It reads the token inside the VNet, calls the existing `/fault` route, and logs a
non-secret JSON result. The attendee laptop never retrieves the credential and
needs no VPN or private-vault data access. The helper caller needs permission to
start/read job executions and query workspace logs, which the required subscription
Owner or Contributor plus User Access Administrator roles include. The retained
attendee vault Secrets User grant is for authorized in-network administration;
local helpers do not use it. The read-only SRE runtime cannot start jobs or read
secrets.

Commands and defaults are unchanged:

| Command | Default arguments |
| --- | --- |
| `status` | No arguments; also the default helper action |
| `cpu` | `600` seconds, `4` threads |
| `errors` | `100` percent, `900` seconds |
| `storage` | `95` percent target |
| `release` | No arguments |
| `reset` | No arguments |

Never display, copy, or persist the token. If access fails, check the selected
environment and subscription, Azure CLI login, caller job/log permissions, and
the job's private networking and managed identity. Reconcile configuration with
`azd up` after resolving the cause. Do not manually set a Container Apps secret
or grant the SRE runtime secret access.

## Fault helper results and retry

After job success, the helper uses the Azure CLI `log-analytics` extension to query
`ContainerAppConsoleLogs_CL` for only that request's non-secret JSON result.
Progress goes to stderr; JSON goes to stdout, so existing JSON consumers can keep
using the same helper commands. The result is correlated by a 32-character
lowercase hexadecimal request ID, not a credential.

During the original call, results are also restricted to the returned execution
name. Identical log records are deduplicated; conflicting results produce an
error instead of an arbitrary success response. Inspect the execution history
rather than reinjecting when results are ambiguous.

Retrieval has a five-minute polling budget for log ingestion after the job
succeeds; an in-flight Azure CLI request can extend the total wait.
The status response is a snapshot captured when the job calls `/fault/status`,
not necessarily the state at log arrival. Fault durations start when the job
invokes the endpoint, not when the helper prints the result. Correlate current
application telemetry with the job's timing rather than treating a delayed status
snapshot as live state.

If logs are delayed, the timeout reports the execution name and request ID.
Keep both, then retry only result retrieval. Replace `<request-id>` with the ID
from that message:

```bash
python scripts/workshop.py fault-result "<request-id>"
```

This command is read-only. It does not start another job, repeat a `/fault`
request, or reinject the fault. Queries are scoped to the last hour (`PT1H`), and
workspace log availability policies still apply. A missing or expired result
does not prove the fault was never applied. Do not reinject to recover a missing
result.

A job reported as failed is different from a successful job with delayed logs.
Inspect the reported execution's logs and the
[troubleshooting guidance](02-troubleshooting.md); credentials and authenticated
response bodies are not printed.

## Refreshing agent content

Edit checked-in Markdown referenced by `agent/knowledge.yaml`, then run
`python scripts/workshop.py configure-agent` for a configuration-only refresh.
`azd up` also synchronizes both knowledge and `agent/incident-filters.yaml`.

<div class="sre-nav" markdown>
[:material-arrow-left: About The Authors](../29-about-the-authors/index.md)
[Troubleshooting :material-arrow-right:](02-troubleshooting.md)
</div>
