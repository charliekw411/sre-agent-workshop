---
title: Workshop Variables and Evidence
description: Reference for the single-VM workshop's non-secret exports, local validation evidence, and supported helper commands.
ms.date: 2026-09-25
ms.topic: reference
keywords:
  - environment variables
  - azure developer cli
  - workshop evidence
estimated_reading_time: 7
---

## Overview

The selected Azure Developer CLI environment stores deployment inputs and
outputs. After `azd up`, the workshop exports an explicit non-secret allowlist to:

* `.workshop/workshop.env` for Bash
* `.workshop/workshop.ps1` for PowerShell

The files contain resource names, IDs, and the public Orders GUI and API URL.
They do not contain a VM private key, API credential, SRE Agent token, or fault
secret.

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

Regenerate the allowlisted files without redeploying:

=== "Bash"

    ```bash
    python scripts/workshop.py export
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py export
    ```

The helper sets the Azure CLI subscription from the selected azd environment
before issuing Azure operations.

## Variable reference

| Variable | Purpose |
| --- | --- |
| `AZURE_ENV_NAME` | Selected azd environment |
| `AZURE_LOCATION`, `LOCATION` | Deployment region |
| `AZURE_SUBSCRIPTION_ID`, `SUBSCRIPTION_ID` | Azure subscription GUID |
| `TENANT_ID` | Microsoft Entra tenant GUID |
| `AZURE_RESOURCE_GROUP`, `RESOURCE_GROUP` | Workshop resource group |
| `WORKSHOP_SUFFIX` | Deterministic resource-name suffix |
| `VM_NAME`, `VM_RESOURCE_ID` | Ubuntu Orders VM |
| `DATA_DISK_NAME`, `DATA_DISK_RESOURCE_ID` | Managed SQLite data disk |
| `PUBLIC_IP_ADDRESS`, `PUBLIC_IP_RESOURCE_ID` | Static public endpoint resource |
| `ORDERS_API_FQDN` | Stable Azure DNS hostname |
| `SERVICE_ORDERS_API_ENDPOINT_URL` | Public Orders GUI and API base URL, including HTTP port 8080 |
| `VIRTUAL_NETWORK_NAME` | Workshop VNet |
| `LOG_ANALYTICS_NAME`, `LOG_ANALYTICS_ID` | Log Analytics workspace |
| `LOG_ANALYTICS_CUSTOMER_ID` | Workspace GUID used by CLI log queries |
| `APP_INSIGHTS_NAME`, `APP_INSIGHTS_RESOURCE_ID` | Workspace-based Application Insights |
| `DATA_COLLECTION_RULE_ID` | Azure Monitor Agent performance collection rule |
| `SRE_AGENT_NAME`, `SRE_AGENT_RESOURCE_ID` | Azure SRE Agent resource |
| `SRE_AGENT_ENDPOINT` | Agent service endpoint used by deployment configuration |
| `SRE_AGENT_PRINCIPAL_ID` | Agent system-assigned identity object ID |
| `SRE_AGENT_IDENTITY_RESOURCE_ID` | Operational user-assigned identity resource |
| `SRE_AGENT_IDENTITY_PRINCIPAL_ID` | Operational identity object ID |

PowerShell accesses the same values through `$env:VARIABLE_NAME`.

Additional values can exist in the private azd environment. Do not replace the
allowlist with a dump of every azd or process environment variable.

## Orders GUI and API URL

Open `SERVICE_ORDERS_API_ENDPOINT_URL` in a normal browser navigation to receive
the Orders GUI. The root uses content negotiation, so command-line clients that
do not request HTML continue to receive the JSON service descriptor. Paths such
as `/orders`, `/health/ready`, and `/storage` always return JSON.

This public endpoint is the unauthenticated customer-facing workshop workload.
It is separate from the GitHub Pages documentation site and its authenticated
Azure incident controls.

## Supported workshop commands

| Command | Purpose |
| --- | --- |
| `python scripts/workshop.py smoke` | Validate public API data and confirm HTTP fault routes are absent |
| `python scripts/workshop.py inspect` | Verify the mount, SQLite integrity, seed rows, and `systemd` service |
| `python scripts/workshop.py telemetry` | Wait for required VM and application telemetry |
| `python scripts/workshop.py verify-restart` | Restart the VM and prove service and data recovery |
| `python scripts/workshop.py fault status` | Read current bounded-fault state through Run Command |
| `python scripts/workshop.py fault cpu [seconds] [workers]` | Start CPU pressure; defaults to 300 seconds and two workers |
| `python scripts/workshop.py fault disk [used-percent] [seconds]` | Start data-disk pressure; defaults to 90 percent for 300 seconds |
| `python scripts/workshop.py fault reset` | Stop fault units and remove only the ballast file |
| `python scripts/workshop.py export` | Rebuild the generated shell exports |

The wrapper scripts call the same fault implementation:

=== "Bash"

    ```bash
    ./scripts/inject-fault.sh status
    ```

=== "PowerShell"

    ```powershell
    ./scripts/inject-fault.ps1 status
    ```

Fault actions use Azure VM Run Command and therefore require Azure CLI
authentication and RBAC. They do not call a public HTTP control endpoint.

## Validation evidence

Commands write JSON evidence under:

```text
.workshop/<environment>/
```

Common files include:

| File | Evidence |
| --- | --- |
| `deployment.json` | Deployment stage durations and outcomes |
| `vm-deployment.json` | Guest installation result |
| `sre-agent-configuration.json` | Response-plan read-back |
| `smoke.json` | Public endpoint and retired-route checks |
| `vm-inspection.json` | Service, mount, disk UUID, and SQLite checks |
| `persistence-witness.json` | Order used across VM restarts |
| `restart.json` | Before/after boot IDs and stable disk UUID |
| `telemetry.json` | Required signal sample counts |
| `fault-cpu.json`, `fault-disk.json` | Fault-start results |
| `fault-status.json`, `fault-reset.json` | Fault state and cleanup results |

These files are ignored by Git. They can contain Azure resource IDs and
operational timestamps, so handle them according to your organization's policy.

Workshop notes belong under `.workshop/notes/` while you work. Copy them outside
deployment state before Module 06 cleanup.

## Authentication is not exported

Azure CLI, Azure Developer CLI, and SRE Agent service authentication use their
normal token caches. Tokens are never written to the workshop export files.

The unused private half of the provisioning SSH key is discarded. The public
key remains as a deployment input, but inbound SSH is blocked by the NSG.
Administrative checks and faults use authenticated Run Command.

## Incident timestamps

Incident start variables such as `INCIDENT_START` are local shell values, not
deployment outputs. Record times in UTC in your notes and use the portal's UTC
time range when comparing charts.

<div class="sre-nav" markdown>
[:material-arrow-left: About the authors](../29-about-the-authors/index.md)
[Troubleshooting :material-arrow-right:](02-troubleshooting.md)
</div>
