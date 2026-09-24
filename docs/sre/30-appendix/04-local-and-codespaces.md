---
title: Running Locally or in Codespaces
description: Preview the documentation, run the SQLite Orders API locally, or deploy the VM workshop from local, Codespaces, or Cloud Shell terminals.
ms.date: 2026-09-24
ms.topic: how-to
keywords:
  - local development
  - github codespaces
  - azure cloud shell
  - mkdocs
estimated_reading_time: 9
---

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

Open [http://localhost:8000](http://localhost:8000). Validate navigation, links,
Markdown, and snippets with:

=== "Bash"

    ```bash
    python -m mkdocs build --strict --site-dir dist
    ```

=== "PowerShell"

    ```powershell
    python -m mkdocs build --strict --site-dir dist
    ```

`make serve` and `make build-docs-website` run equivalent commands on systems
with GNU Make.

## Run the Orders API locally

Local application development requires the .NET 8 SDK. It does not reproduce
the Azure VM, managed disk, `systemd`, Run Command, Azure Monitor, or SRE Agent.

Create a local SQLite database outside the source project:

=== "Bash"

    ```bash
    mkdir -p .local
    export ConnectionStrings__OrdersDb="Data Source=$(pwd)/.local/orders.db"
    dotnet run --project src/OrdersApi -- --bootstrap
    dotnet run --project src/OrdersApi --urls http://localhost:8080
    ```

=== "PowerShell"

    ```powershell
    New-Item -ItemType Directory -Force .local | Out-Null
    $database = Join-Path (Resolve-Path .) '.local\orders.db'
    $env:ConnectionStrings__OrdersDb = "Data Source=$database"
    dotnet run --project src/OrdersApi -- --bootstrap
    dotnet run --project src/OrdersApi --urls http://localhost:8080
    ```

In another terminal:

=== "Bash"

    ```bash
    curl --silent --fail http://localhost:8080/orders
    curl --silent --fail \
      --request POST http://localhost:8080/orders \
      --header 'Content-Type: application/json' \
      --data '{"customerId":"local","productId":"SKU-1001","quantity":1}'
    ```

=== "PowerShell"

    ```powershell
    Invoke-RestMethod http://localhost:8080/orders

    $body = @{
      customerId = 'local'
      productId  = 'SKU-1001'
      quantity   = 1
    } | ConvertTo-Json

    Invoke-RestMethod `
      -Method Post `
      -Uri http://localhost:8080/orders `
      -ContentType 'application/json' `
      -Body $body
    ```

Do not run `scripts/vm/faults.py` locally. It is designed for root execution
through authenticated Run Command and refuses unexpected mounts.

## Deploy from GitHub Codespaces

Codespaces provides a browser-hosted terminal. Verify rather than assume that the
chosen image contains every prerequisite:

=== "Bash"

    ```bash
    az version
    azd version
    python --version
    ssh-keygen -V 2>&1 | head -1 || true
    python -m pip install -r requirements.txt
    ```

=== "PowerShell"

    ```powershell
    az version
    azd version
    python --version
    Get-Command ssh-keygen | Select-Object -ExpandProperty Source
    python -m pip install -r requirements.txt
    ```

Authenticate using the device flow when prompted:

=== "Bash"

    ```bash
    az login --use-device-code
    az account set --subscription "<subscription-id>"
    azd auth login
    ```

=== "PowerShell"

    ```powershell
    az login --use-device-code
    az account set --subscription "<subscription-id>"
    azd auth login
    ```

Then follow Module 01. Forward port 8000 only when previewing MkDocs. The Azure
Orders API remains on its own public Azure DNS endpoint.

Codespaces can suspend an inactive terminal. Do not rely on it to keep the
load-generator process alive while you leave the browser. The guest faults
remain bounded even if the client disconnects.

## Deploy from Azure Cloud Shell

Open [Azure Cloud Shell](https://shell.azure.com), choose Bash or PowerShell,
and clone the repository:

=== "Bash"

    ```bash
    git clone https://github.com/charliekw411/sre-agent-workshop.git
    cd sre-agent-workshop
    az account show --output table
    azd version
    python -m pip install -r requirements.txt
    ```

=== "PowerShell"

    ```powershell
    git clone https://github.com/charliekw411/sre-agent-workshop.git
    Set-Location sre-agent-workshop
    az account show --output table
    azd version
    python -m pip install -r requirements.txt
    ```

Install or update azd if the reported version is earlier than 1.18. Cloud Shell
already has an Azure CLI login, but `azd auth login` is still required.

Cloud Shell storage is persistent only when configured for your session. Copy
workshop notes somewhere durable before cleanup or session reset.

## Bash and PowerShell helper parity

After deployment:

=== "Bash"

    ```bash
    source .workshop/workshop.env
    ./scripts/inject-fault.sh status
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    ./scripts/inject-fault.ps1 status
    ```

Both wrappers call `scripts/workshop.py` and use authenticated Azure VM Run
Command. The API URL is available as `SERVICE_ORDERS_API_ENDPOINT_URL` in both
shells.

The provided sustained load generator is Bash. PowerShell alternatives are
included in the modules where traffic is required.

<div class="sre-nav" markdown>
[:material-arrow-left: Cost Management](03-cost-management.md)
[Workshop home :material-arrow-right:](../../index.md)
</div>
