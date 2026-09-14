---
title: Module 01 - Prerequisites
description: Verify tooling, both Azure logins, subscription permissions, and preview regional availability before azd up.
ms.date: 2026-09-14
ms.topic: how-to
keywords:
  - prerequisites
  - azure cli
  - permissions
estimated_reading_time: 10
---

<ul class="sre-meta">
<li class="duration">Estimated time: 20 minutes</li>
<li>Module 01</li>
<li>Hands-on</li>
</ul>

## Overview

Deployment is automated, but it still needs compatible tools and subscription
permissions. Complete these checks before running `azd up`.

## Learning objectives

* Authenticate Azure CLI and Azure Developer CLI separately.
* Confirm subscription-level resource creation and role assignment permissions.
* Prepare Python-based hooks on Bash or PowerShell.
* Select a region that supports the SRE Agent preview.

## Tasks

### Task 1: Clone the repository and verify tools

```bash
git clone https://github.com/charliekw411/sre-agent-workshop.git
cd sre-agent-workshop
az version
azd version
python --version
```

Required tools:

| Tool | Requirement |
| --- | --- |
| Azure Developer CLI | Version 1.18 or later |
| Azure CLI | Version 2.60 or later; accessible on the same PATH as the hooks |
| Python | Python 3.10 or later with PyYAML; use `python3` instead of `python` where needed |
| Shell | Bash or PowerShell |
| Git | Clone the repository |
| `jq` and `curl` | Bash investigation and validation examples |

The hooks share `scripts/workshop.py` across platforms. Install the existing
repository dependencies in an activated virtual environment:

=== "Bash"

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    python -c "import yaml; print(yaml.__version__)"
    ```

=== "PowerShell"

    ```powershell
    python -m venv .venv
    . ./.venv/Scripts/Activate.ps1
    python -m pip install -r requirements.txt
    python -c "import yaml; print(yaml.__version__)"
    ```

The PowerShell wrapper checks the Python version and prefers `python` on Windows
to avoid the `python3` Windows Store alias; on Linux it prefers `python3`.

Install missing Azure tools using the official
[Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) and
[Azure Developer CLI](https://learn.microsoft.com/azure/developer/azure-developer-cli/install-azd)
instructions. No local Docker daemon or .NET SDK is required: Azure Container
Registry builds the .NET 8 application images remotely. A local .NET 8 SDK is only
needed if you choose to develop or build the sample services locally.

### Task 2: Authenticate both CLIs

```bash
az login
az account set --subscription "<your-subscription-id>"
az account show --output table
azd auth login
```

Azure CLI and `azd` maintain separate authentication contexts. Use the same tenant,
account, and subscription for the deployment; the hooks use Azure CLI to resolve
your identity and configure the SRE Agent. Signing in to only one CLI is not enough.

azd supplies the built-in `AZURE_PRINCIPAL_ID` and `AZURE_PRINCIPAL_TYPE` values
before pre-provision hooks run; do not create custom deployer identity variables.
The hook compares both CLIs' ARM token `oid` and `tid` claims in memory and stops
if the signed-in identities or tenants differ. Tokens are not printed or saved.

### Task 3: Verify subscription permissions

You need **subscription Owner**, or **subscription Contributor plus User Access
Administrator**. Resource-group-only access is insufficient because deployment
creates the resource group. Contributor alone cannot assign the managed identity
and attendee roles required by the deployment.

```bash
az role assignment list \
  --assignee "$(az ad signed-in-user show --query id --output tsv)" \
  --scope "/subscriptions/$(az account show --query id --output tsv)" \
  --include-inherited \
  --query "[].{Role:roleDefinitionName, Scope:scope}" --output table
```

Have your subscription administrator provide the required access before you
continue. Do not work around failed deployment by manually granting runtime roles.
`azd up` owns those assignments, including the attendee's agent-scoped
`SRE Agent Administrator` role. Runtime access is deliberately read-only; see the
[permission record](../05-configure-sre-agent/index.md#deployment-api-contract-and-permission-record).

### Task 4: Prepare tooling extensions

```bash
az bicep install
az extension add --name containerapp --upgrade
az extension add --name application-insights --upgrade
az extension add --name log-analytics --upgrade
```

The deployment caller must be allowed to register resource providers.
The pre-provision hook automatically registers required providers, including
`Microsoft.App`, `Microsoft.ContainerRegistry`, `Microsoft.OperationalInsights`,
`Microsoft.Insights`, `Microsoft.Sql`, `Microsoft.ManagedIdentity`,
`Microsoft.KeyVault`, `Microsoft.AlertsManagement`, `Microsoft.Monitor`,
`Microsoft.ContainerInstance`, and `Microsoft.Storage`. The last two support
the deployment script. The hook waits up to fifteen minutes for registration;
no manual registration step is required.

### Task 5: Select the environment and supported region

```bash
azd env new "<your-alias>-workshop"
azd env set AZURE_LOCATION eastus2
```

If you already created the environment in Module 00, select it with
`azd env select "<your-alias>-workshop"` instead. The supported default is
`eastus2`. SRE Agent uses `Microsoft.App/agents@2025-05-01-preview`, with constrained
region and subscription availability. Check the
[Azure SRE Agent documentation](https://learn.microsoft.com/azure/sre-agent/)
before changing regions. Container Apps availability alone does not establish
SRE Agent availability.

Before provisioning, the hook validates `AZURE_LOCATION` against the advertised
supported locations for `Microsoft.App/agents` and fails early for an unsupported
selection. This check does not override subscription policy or preview access
restrictions.

!!! warning "Corporate subscription policies"
    This workshop intentionally uses public application ingress and public SQL network access. Policies that require private endpoints or deny preview resources can block deployment. Use an approved workshop subscription rather than bypassing policy.

## Validation

```bash
az account show --output table
azd version
python -c "import sys, yaml; print(sys.version); print(yaml.__version__)"
azd env get-value AZURE_ENV_NAME
azd env get-value AZURE_LOCATION
```

## Expected results

Both CLIs are authenticated, tools are available, subscription permissions have
been checked, and your named environment selects a supported region. The
`.workshop/` shell exports are generated after deployment; they are not a
prerequisite to these checks.

## Knowledge check

??? question "Is Contributor alone sufficient?"
    No. The deployment creates role assignments as well as resources. Use subscription Owner or Contributor plus User Access Administrator.

??? question "Do remote builds require Docker or .NET on the attendee machine?"
    No. Those build tools run in Azure Container Registry. The attendee needs the CLIs and Python hook dependencies.

## Next steps

[Next: Module 02 - Solution Architecture :material-arrow-right:](../02-solution-architecture/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: How This Workshop Works](../00-workshop-intro/3-how-this-workshop-works.md)
[Module 02 - Solution Architecture :material-arrow-right:](../02-solution-architecture/index.md)
</div>
