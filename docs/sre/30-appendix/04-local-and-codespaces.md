---
title: Running Locally or in Codespaces
description: Preview documentation, develop the sample services, and deploy the workshop from a local or browser-hosted terminal.
ms.date: 2026-09-14
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
job. Use `azd up` for the integrated workshop rather than a local SQL administrator
connection string. Do not reuse the bootstrap identity as an application identity.

Fault operations against the deployed app use `./scripts/inject-fault.sh` or
`./scripts/inject-fault.ps1`. The common Python helper retrieves the secret from
Key Vault just in time; do not save credentials in local development settings.

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
