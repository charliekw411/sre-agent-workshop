---
title: Running Locally or in Codespaces
description: Preview documentation, develop the sample services, and deploy the workshop from a local or browser-hosted terminal.
ms.date: 2026-09-21
ms.topic: how-to
keywords:
  - local development
  - codespaces
  - cloud shell
  - mkdocs
estimated_reading_time: 7
---

## Overview

Local terminals, Codespaces, and Azure Cloud Shell all use the same `azd up`
deployment. None needs a local Docker daemon or .NET SDK for deployment: images
build remotely in Azure Container Registry. All need Azure CLI, `azd` 1.18 or
later, Python 3.10 or later, and PyYAML.

## Preview the documentation site

The site uses MkDocs and Material for MkDocs.

=== "Bash"

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    python -m mkdocs serve
    ```

=== "PowerShell"

    ```powershell
    python -m venv .venv
    . ./.venv/Scripts/Activate.ps1
    python -m pip install -r requirements.txt
    python -m mkdocs serve
    ```

Open [http://localhost:8000](http://localhost:8000). Changes under `docs/` reload
automatically. Validate documentation using the existing strict build:

```bash
python -m mkdocs build --strict --site-dir dist
```

The Makefile's `make build-docs-website` target installs the same requirements
and runs this build. `make serve` installs them and starts the preview.

## Local application development

Local source development, unlike attendee deployment, requires the .NET 8 SDK.
The catalog service can run independently:

```bash
cd src/CatalogApi
dotnet run --urls http://localhost:8081
```

The full Orders API path depends on an Entra-only Azure SQL database, its contained
runtime user, and object-level grants initialized by the separate managed identity
job. SQL public network access is disabled; the deployed apps and bootstrap job
reach it through a private endpoint and VNet-linked DNS. A standalone local Orders
process does not gain that private network access from an Azure CLI login.
Use `azd up` for the integrated workshop rather than a local SQL administrator
connection string or public firewall workaround. Do not reuse the bootstrap
identity as an application identity.

Fault operations against the deployed app use `./scripts/inject-fault.sh` or
`./scripts/inject-fault.ps1` with the same commands and defaults. The common Python
helper starts and waits for `workshop-fault-client` through ARM. The job retrieves
the credential inside the VNet; your laptop, Codespace, or Cloud Shell never
retrieves it and needs no VPN or private-vault data access. The helper uses the
Azure CLI `log-analytics` extension to retrieve only the non-secret correlated
JSON result. Do not save credentials in local development settings.

Results can take up to five minutes to arrive after job success, and status is
a job-captured snapshot rather than live state at log arrival. If retrieval
times out, use `python scripts/workshop.py fault-result <request-id>` with the
reported 32-character ID to retry only read-only log retrieval. Do not reinject.
Queries cover the last hour (`PT1H`), subject to log availability policies.
See [fault helper results](01-variables.md#fault-helper-results-and-retry).

## GitHub Codespaces

Codespaces provides a browser-hosted terminal. Verify tool availability rather
than assuming the selected image includes all prerequisites.

1. Open the repository on GitHub.
2. Select **Code**, **Codespaces**, then **Create codespace on main**.
3. Complete [Module 01](../01-prerequisites/index.md), including dependencies and
   both authentication contexts:

```bash
az login --use-device-code
azd auth login
azd version
python -c "import yaml"
```

Then select your named environment and run `azd up`. When running the MkDocs
preview, forward port 8000 through Codespaces.

## Azure Cloud Shell

Open [Azure Cloud Shell](https://shell.azure.com), select Bash, and clone the
repository. Check tool versions and install the existing Python dependencies in
a virtual environment as shown above.

```bash
git clone https://github.com/charliekw411/sre-agent-workshop.git
cd sre-agent-workshop
az login
azd auth login
azd version
```

Cloud Shell authentication does not remove the need to check both CLIs. Use the
same tenant and subscription and complete the permissions checks in Module 01.
Cloud Shell sessions can time out and interrupt the long-running load generator,
so keep its terminal active or run the generator from a local terminal.

## Bash and PowerShell exports

After deployment:

```bash
source .workshop/workshop.env
./scripts/inject-fault.sh status
```

```powershell
. ./.workshop/workshop.ps1
./scripts/inject-fault.ps1 status
```

The generated files contain allowlisted non-secret identifiers and endpoints.
PowerShell accesses these as `$env:RESOURCE_GROUP`, for example. Agent content
refresh uses the same cross-platform command:
`python scripts/workshop.py configure-agent`.

<div class="sre-nav" markdown>
[:material-arrow-left: Cost Management](03-cost-management.md)
[Homepage :material-arrow-right:](../../index.md)
</div>
