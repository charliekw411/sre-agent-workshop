---
title: Running Locally or in Codespaces
description: Run the sample application on your own machine, preview the documentation site locally, and use GitHub Codespaces or Azure Cloud Shell for the workshop.
ms.date: 2026-09-08
ms.topic: how-to
keywords:
  - local development
  - codespaces
  - cloud shell
  - mkdocs
estimated_reading_time: 7
---

## Overview

Three scenarios are covered here: previewing the documentation site, running the sample application locally, and running the workshop from a browser without installing anything.

## Preview the documentation site

The site is built with MkDocs and Material for MkDocs, the same toolchain as the [Azure Container Apps .NET Workshop](https://azure.github.io/aca-dotnet-workshop/).

```bash
python3 -m venv .venv
source .venv/bin/activate
make serve
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m mkdocs serve
```

Open [http://localhost:8000](http://localhost:8000). Edits to files under `docs/` reload automatically.

Before opening a pull request, confirm the strict build passes. Continuous integration runs the same command, and it fails on broken internal links and missing assets.

```bash
make build-docs-website
```

## Run the sample application locally

Both services run on .NET 8. `catalog-api` has no dependencies and starts immediately.

```bash
cd src/CatalogApi
dotnet run --urls http://localhost:8081
```

In a second terminal, start `orders-api` pointing at it.

```bash
cd src/OrdersApi
export Catalog__BaseUrl="http://localhost:8081"
export Fault__Enabled="true"
export Fault__Token="local-development-token"
export ConnectionStrings__OrdersDb="Server=localhost,1433;Database=sqldb-orders;User ID=sa;Password=<your-local-password>;Encrypt=True;TrustServerCertificate=True;"
dotnet run --urls http://localhost:8080
```

For local SQL Server, run it in a container.

```bash
docker run --rm --detach \
  --name sqlserver \
  --env ACCEPT_EULA=Y \
  --env "MSSQL_SA_PASSWORD=<your-local-password>" \
  --publish 1433:1433 \
  mcr.microsoft.com/mssql/server:2022-latest
```

!!! warning "Local SQL credentials"
    Replace `<your-local-password>` with a value you generate. Do not reuse a password from any other environment, and do not commit it. The container is intended for local development only and exposes SQL Server on all interfaces.

Verify the pair works.

```bash
curl --silent http://localhost:8080/ | jq .

curl --silent --request POST http://localhost:8080/orders \
  --header 'Content-Type: application/json' \
  --data '{"customerId":"cust-001","productId":"SKU-1002","quantity":1}' | jq .

curl --silent --header "X-Fault-Token: local-development-token" \
  http://localhost:8080/fault/status | jq .
```

The application starts even when the database is unreachable. Order writes fail, everything else works, and the failure is logged rather than fatal. That is intentional; a crash loop before telemetry is emitted is worse than a running service that reports what is broken.

## GitHub Codespaces

Codespaces gives you the Azure CLI, .NET, Python, and Docker without local installation.

1. Open the repository on GitHub.
2. Select **Code**, then **Codespaces**, then **Create codespace on main**.
3. In the Codespaces terminal:

```bash
az login --use-device-code
make install
chmod +x scripts/*.sh
```

Then start at [Module 01](../01-prerequisites/index.md). Every command in the workshop works unchanged.

!!! tip "Port forwarding"
    When you run `make serve` in a Codespace, port 8000 is forwarded automatically and a preview link appears. The same applies to ports 8080 and 8081 if you run the sample application there.

## Azure Cloud Shell

Cloud Shell is the fastest path if you cannot install software locally. It includes the Azure CLI, Bicep, `jq`, and Git.

1. Open [https://shell.azure.com](https://shell.azure.com) and select Bash.
2. Clone the repository.

```bash
git clone https://github.com/charliekw411/sre-agent-workshop.git
cd sre-agent-workshop
chmod +x scripts/*.sh
```

Two constraints apply. Cloud Shell sessions time out after roughly 20 minutes of inactivity, which will interrupt the load generator from Module 04, so run that from a session you keep active or from a local terminal. Cloud Shell also has no Docker daemon, which is why the workshop uses `az acr build` rather than local image builds.

## Editor setup

For editing the workshop content, these extensions help:

| Extension                 | Purpose                                        |
|---------------------------|------------------------------------------------|
| `ms-azuretools.vscode-bicep` | Bicep authoring, validation, and formatting |
| `ms-dotnettools.csdevkit`    | C# language support for the sample services |
| `davidanson.vscode-markdownlint` | Markdown linting matching the CI rules  |
| `ms-vscode.azurecli`         | Azure CLI IntelliSense in shell scripts     |

<div class="sre-nav" markdown>
[:material-arrow-left: Cost Management](03-cost-management.md)
[Homepage :material-arrow-right:](../../index.md)
</div>
