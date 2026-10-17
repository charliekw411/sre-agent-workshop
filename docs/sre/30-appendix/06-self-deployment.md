---
title: Self-deployment (Optional)
description: Deploy and later delete your own independent workshop environment, outside the participant learning path.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - azure developer cli
  - independent workshop
  - deployment
estimated_reading_time: 7
---

Use this guide only when you are running the workshop independently and own
the environment. At an organized event, your infrastructure is already
deployed; start [Module 01](../01-deploy-and-validate/index.md) instead.

## Prerequisites

From a repository checkout, you need:

* Subscription Owner, or Contributor plus User Access Administrator, for
  deployment and role assignments
* Azure CLI 2.60+ and Azure Developer CLI 1.18+
* Python 3.10+ with `requirements.txt`
* Bash with `curl` and `jq`, or PowerShell 7
* OpenSSH `ssh-keygen`

Deployment does not need local Docker or a .NET SDK. The VM installs and
builds the app. [Application tests](04-local-and-codespaces.md) have separate
requirements.

## Deploy a new environment

Sign Azure CLI and azd in with the same identity and tenant. Use a new
environment name:

=== "Bash"

    ```bash
    az login
    az account set --subscription "<your-subscription-id>"
    azd auth login
    azd env new "<your-alias>-sre-vm-aue"
    azd env set AZURE_LOCATION australiaeast
    python -m pip install -r requirements.txt
    azd up
    ```

=== "PowerShell"

    ```powershell
    az login
    az account set --subscription "<your-subscription-id>"
    azd auth login
    azd env new "<your-alias>-sre-vm-aue"
    azd env set AZURE_LOCATION australiaeast
    python -m pip install -r requirements.txt
    azd up
    ```

Preflight checks identity, providers, regional service availability, VM quota,
and PostgreSQL availability. It does not request quota, select another
region/SKU, or create policy exemptions. The default VM is `Standard_D2as_v5`;
PostgreSQL is version 16 on `Standard_B1ms`.

The resource group is `rg-sre-agent-workshop-<environment>`, tagged
`workshop-architecture=single-vm-postgresql-v1`. A group with a different tag
is rejected, not migrated or deleted.

The deployment creates the VM, private database and network, monitoring,
alerts, and SRE Agent. Its hooks install the app, prepare the schema and seed
orders, and configure the Review response plan. It exports only allowlisted
non-secret values.

If a stage fails, read its error and evidence under `.workshop/<environment>/`.
Use [owner troubleshooting](02-troubleshooting.md#organizer-and-owner-diagnostics)
before rerunning. Repeated `azd up` preserves orders and restores the normal
database rule to `Allow`.

## Check the deployment

=== "Bash"

    ```bash
    source .workshop/workshop.env
    python scripts/workshop.py smoke
    python scripts/workshop.py inspect
    python scripts/workshop.py telemetry
    ```

=== "PowerShell"

    ```powershell
    . .\.workshop\workshop.ps1
    python .\scripts\workshop.py smoke
    python .\scripts\workshop.py inspect
    python .\scripts\workshop.py telemetry
    ```

Expect healthy public routes, private PostgreSQL connectivity, and samples
for requests, database calls, availability, and VM monitoring. `inspect`
checks private DNS, encrypted connections, VM identity, schema, and service
health. Monitoring may need several minutes to catch up.

The optional `python scripts/workshop.py verify-restart` check restarts only
the VM and verifies that an order survives in the same database.

## Prepare the browser exercises

You act as both organizer and participant for an independent run:

* Prepare the skill/custom agent and Live Report using the
  [proactive setup guide](08-proactive-extensions.md).
* Configure a local or separately hosted site for your tenant using
  [site connection setup](05-site-azure-connection.md). The shared workshop
  site accepts only its configured tenant.
* Use your deployment account and outputs in place of a facilitator handout.

The deployer has SRE Agent Administrator access. Keep the agent's runtime
permissions read-only. Then follow the five browser modules.

## Delete your own environment

Save any evidence you want and reset both faults with **Reset incident** in the
CPU and PostgreSQL launchers first, then follow
[Module 05](../06-cleanup/index.md). Select your own local azd environment
and regenerate its exports as described in
[terminal setup](01-variables.md#terminal-setup-for-an-assigned-environment).

Review the group before deletion:

=== "Bash"

    ```bash
    printf 'Environment: %s\nResource group: %s\n' \
      "${AZURE_ENV_NAME}" "${RESOURCE_GROUP}"
    az resource list --resource-group "${RESOURCE_GROUP}" \
      --subscription "${AZURE_SUBSCRIPTION_ID}" --output table
    python scripts/workshop.py health-check remove
    azd down --purge
    az group exists --name "${RESOURCE_GROUP}" \
      --subscription "${AZURE_SUBSCRIPTION_ID}"
    ```

=== "PowerShell"

    ```powershell
    "Environment: $env:AZURE_ENV_NAME"
    "Resource group: $env:RESOURCE_GROUP"
    az resource list --resource-group $env:RESOURCE_GROUP `
      --subscription $env:AZURE_SUBSCRIPTION_ID --output table
    python .\scripts\workshop.py health-check remove
    azd down --purge
    az group exists --name $env:RESOURCE_GROUP `
      --subscription $env:AZURE_SUBSCRIPTION_ID
    ```

Stop if the scope is not yours. Read the deletion confirmation. Deletion
permanently removes the VM, PostgreSQL and its orders/backups, monitoring,
agent, and reports. The group-existence result should eventually be `false`.
If deletion is incomplete, use the [cleanup diagnostics](02-troubleshooting.md#cleanup).

After verification, you can remove the obsolete local metadata with
`azd env remove "<your-environment>"`. Keep saved review artifacts.

<div class="sre-nav" markdown>
[Module 01 - Access and Understand the Workshop](../01-deploy-and-validate/index.md)
[Organizer guide :material-arrow-right:](07-organizer-guide.md)
</div>
