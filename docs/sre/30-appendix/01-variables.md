---
title: Workshop Variables
description: Reference for the safe Bash and PowerShell deployment exports and just-in-time fault authentication.
ms.date: 2026-09-14
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
| `KEY_VAULT_NAME` | Vault used for just-in-time fault secret retrieval |
| `CONTAINER_ENV_NAME` | Container Apps managed environment |
| `ORDERS_API_FQDN` | Public API hostname |
| `SRE_AGENT_NAME`, `SRE_AGENT_PRINCIPAL_ID` | Agent resource name and runtime identity object ID |
| `SRE_AGENT_RESOURCE_ID`, `SRE_AGENT_ENDPOINT` | Agent ARM resource ID and configuration API endpoint |

PowerShell reads the exports as `$env:RESOURCE_GROUP`, `$env:ORDERS_API_FQDN`,
and so on. Incident start timestamps are session notes recorded during Modules
06, 08, and 10 rather than infrastructure outputs.

The deployment parameters also use azd's built-in `AZURE_PRINCIPAL_ID` and
`AZURE_PRINCIPAL_TYPE` for attendee identity resolution. These are azd inputs,
not additional workshop shell exports or custom variables you must fill in.

## Rebuilding the files

Select the correct environment and run `python scripts/workshop.py export` to
regenerate the allowlisted outputs from existing deployment state. `azd up`
also regenerates them through the hooks. Do not reconstruct them by dumping
every azd or shell variable: that bypasses the non-secret allowlist.
Redeployment inserts only missing SQL seed IDs and preserves existing orders
and ballast.

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

Fault credentials are stored in Key Vault and retrieved just in time by the
common Python implementation behind these wrappers:

```bash
./scripts/inject-fault.sh status
```

```powershell
./scripts/inject-fault.ps1 status
```

Never display, copy, or persist the retrieved token. If access fails, check the
selected subscription, Azure CLI login, deployment-assigned Key Vault access,
and permission propagation, then rerun `azd up` to reconcile configuration.
Do not manually set a Container Apps secret or grant the SRE runtime secret access.

## Refreshing agent content

Edit checked-in Markdown referenced by `agent/knowledge.yaml`, then run
`python scripts/workshop.py configure-agent` for a configuration-only refresh.
`azd up` also synchronizes both knowledge and `agent/incident-filters.yaml`.

<div class="sre-nav" markdown>
[:material-arrow-left: About The Authors](../29-about-the-authors/index.md)
[Troubleshooting :material-arrow-right:](02-troubleshooting.md)
</div>
