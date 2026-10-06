---
title: Running Locally or in Codespaces
description: Preview the site, test the Orders API with real PostgreSQL 16, run a disposable local database, or deploy Azure from local and hosted terminals.
ms.date: 2026-10-06
ms.topic: how-to
keywords:
  - local development
  - testcontainers
  - postgresql
  - github codespaces
  - mkdocs
estimated_reading_time: 13
---

## Choose the workflow you need

The repository has three distinct workflows:

| Workflow | Database and infrastructure | Required locally |
| --- | --- | --- |
| Documentation preview | No application database | Python 3.10+, Node.js 20+ |
| Application build and tests | Real PostgreSQL 16 through Testcontainers or `ORDERS_TEST_POSTGRES_CONNECTION_STRING` | .NET 8 SDK; Docker for the default path |
| Azure workshop deployment | Private PostgreSQL Flexible Server and Orders VM provisioned by azd | Azure CLI, azd, Python, and OpenSSH; no local Docker or .NET SDK |

Passing local application tests does not validate Azure private DNS, delegated
subnets, managed identity, TLS `VerifyFull`, systemd, Azure Monitor, alerts, or
SRE Agent. A successful Azure deployment does not replace application tests.

## Preview the documentation

The site uses Material for MkDocs and a locally bundled MSAL Browser dependency.

=== "Bash"

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install -r requirements.txt
    npm ci
    npm run build:web
    python -m mkdocs serve
    ```

=== "PowerShell"

    ```powershell
    python -m venv .venv
    . ./.venv/Scripts/Activate.ps1
    python -m pip install -r requirements.txt
    npm ci
    npm run build:web
    python -m mkdocs serve
    ```

Open
[http://localhost:8000/sre-agent-workshop/](http://localhost:8000/sre-agent-workshop/).
Without an Entra SPA configuration, public documentation renders and Azure
controls fail closed.

Validate the site:

```bash
npm test
npm run build:web
python -m mkdocs build --strict --site-dir dist
```

`make serve` and `make build-docs-website` run equivalent commands on systems
with GNU Make.

## Run application tests with Testcontainers

The test project uses `Testcontainers.PostgreSql` and the
`postgres:16-alpine` image. It does not use an in-memory substitute.

Prerequisites:

* .NET 8 SDK, or a newer SDK with the .NET 8 runtime.
* A running Docker-compatible daemon.
* Permission to pull and start `postgres:16-alpine`.

Verify the daemon before starting:

=== "Bash"

    ```bash
    dotnet --info
    docker info
    docker pull postgres:16-alpine
    dotnet test src/OrdersApi.Tests/OrdersApi.Tests.csproj --configuration Release
    ```

=== "PowerShell"

    ```powershell
    dotnet --info
    docker info
    docker pull postgres:16-alpine
    dotnet test src/OrdersApi.Tests/OrdersApi.Tests.csproj --configuration Release
    ```

When `ORDERS_TEST_POSTGRES_CONNECTION_STRING` is absent, the fixture:

1. Generates an ephemeral password.
2. Starts PostgreSQL 16 through Testcontainers.
3. Waits for the container to become usable.
4. Verifies the server major version is 16.
5. Creates a unique `orders_test_<random>` database for each test scope.
6. Applies the real schema bootstrap.
7. Forcibly drops each isolated test database.
8. Disposes the container after the non-parallel PostgreSQL test collection.

Testcontainers normally handles readiness and teardown. If the test host is
forcibly terminated, inspect containers before removing only the orphan that
belongs to this run:

```bash
docker ps --all --filter "ancestor=postgres:16-alpine"
docker rm --force "<confirmed-test-container-id>"
```

Do not use a broad command that removes unrelated developer containers.

## Use an external PostgreSQL 16 test server

Set `ORDERS_TEST_POSTGRES_CONNECTION_STRING` when Docker is unavailable or CI
already supplies PostgreSQL. The value must contain `Host`, `Database`,
`Username`, and `Password`, and the server must be PostgreSQL 16.

!!! danger "Use a disposable administrative target"
    Tests create and forcibly drop databases named `orders_test_<random>`.
    Never point this variable at production or a shared server. Use an
    ephemeral password and remove the service after the test job.

=== "Bash"

    ```bash
    export ORDERS_TEST_POSTGRES_CONNECTION_STRING='Host=127.0.0.1;Port=5432;Database=postgres;Username=postgres;Password=<ephemeral-password>;SSL Mode=Disable'
    dotnet test src/OrdersApi.Tests/OrdersApi.Tests.csproj --configuration Release
    unset ORDERS_TEST_POSTGRES_CONNECTION_STRING
    ```

=== "PowerShell"

    ```powershell
    $env:ORDERS_TEST_POSTGRES_CONNECTION_STRING = 'Host=127.0.0.1;Port=5432;Database=postgres;Username=postgres;Password=<ephemeral-password>;SSL Mode=Disable'
    try {
      dotnet test `
        src/OrdersApi.Tests/OrdersApi.Tests.csproj `
        --configuration Release
    }
    finally {
      Remove-Item Env:ORDERS_TEST_POSTGRES_CONNECTION_STRING `
        -ErrorAction SilentlyContinue
    }
    ```

This password path exists only for local or CI tests. Azure workshop deployment
uses Microsoft Entra managed identity and does not emit a database credential.

## Run a reliable local PostgreSQL and API

This workflow exercises the browser GUI and JSON API without Azure. It still
does not reproduce the Azure identity, private network, monitoring, or fault
controls.

### Bash

Use one terminal. The trap removes the named container when the API exits:

```bash
CONTAINER="orders-local-postgres"
POSTGRES_PASSWORD="local-$(python -c 'import secrets; print(secrets.token_hex(16))')"

cleanup() {
  docker stop "${CONTAINER}" > /dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

docker run --detach --rm \
  --name "${CONTAINER}" \
  --publish 127.0.0.1:55432:5432 \
  --env POSTGRES_DB=orders_local \
  --env POSTGRES_USER=postgres \
  --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
  postgres:16-alpine

until docker exec "${CONTAINER}" \
  pg_isready --username postgres --dbname orders_local > /dev/null 2>&1; do
  sleep 2
done

export ConnectionStrings__OrdersDb="Host=127.0.0.1;Port=55432;Database=orders_local;Username=postgres;Password=${POSTGRES_PASSWORD};SSL Mode=Disable"
export OrdersDatabase__Authentication=Password

dotnet run --project src/OrdersApi -- --bootstrap
dotnet run --project src/OrdersApi --urls http://localhost:8080
```

Press ++ctrl+c++ when finished. The shell trap stops the container, and
`--rm` removes it.

### PowerShell

`finally` performs teardown even when a command fails:

```powershell
$container = 'orders-local-postgres'
$password = "local-$([guid]::NewGuid().ToString('N'))"

try {
  docker run --detach --rm `
    --name $container `
    --publish 127.0.0.1:55432:5432 `
    --env POSTGRES_DB=orders_local `
    --env POSTGRES_USER=postgres `
    --env "POSTGRES_PASSWORD=$password" `
    postgres:16-alpine
  if ($LASTEXITCODE -ne 0) {
    throw 'PostgreSQL container did not start.'
  }

  do {
    docker exec $container `
      pg_isready --username postgres --dbname orders_local *> $null
    if ($LASTEXITCODE -ne 0) {
      Start-Sleep -Seconds 2
    }
  } while ($LASTEXITCODE -ne 0)

  $env:ConnectionStrings__OrdersDb = "Host=127.0.0.1;Port=55432;Database=orders_local;Username=postgres;Password=$password;SSL Mode=Disable"
  $env:OrdersDatabase__Authentication = 'Password'

  dotnet run --project src/OrdersApi -- --bootstrap
  if ($LASTEXITCODE -ne 0) {
    throw 'Schema bootstrap failed.'
  }
  dotnet run --project src/OrdersApi --urls http://localhost:8080
}
finally {
  Remove-Item Env:ConnectionStrings__OrdersDb -ErrorAction SilentlyContinue
  Remove-Item Env:OrdersDatabase__Authentication -ErrorAction SilentlyContinue
  docker stop $container *> $null
}
```

Open [http://localhost:8080/](http://localhost:8080/) while the application
runs. The status cards call `/health/live`, `/health/ready`, and `/database`.

In another terminal:

=== "Bash"

    ```bash
    curl --silent --fail http://localhost:8080/database | jq .
    curl --silent --fail http://localhost:8080/orders | jq .
    curl --silent --fail \
      --request POST http://localhost:8080/orders \
      --header 'Content-Type: application/json' \
      --data '{"customerId":"local","productId":"SKU-1001","quantity":1}' | jq .
    ```

=== "PowerShell"

    ```powershell
    Invoke-RestMethod http://localhost:8080/database
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
through authenticated Azure VM Run Command. The PostgreSQL incident is an Azure
NSG rule deployment and has no local equivalent.

## Deploy Azure from a local terminal

Azure deployment needs:

* Azure CLI 2.60 or later.
* Azure Developer CLI 1.18 or later.
* Python 3.10 or later with `requirements.txt`.
* OpenSSH `ssh-keygen`.
* Bash with `curl` and `jq`, or PowerShell 7.

It does not use the local PostgreSQL container or
`ORDERS_TEST_POSTGRES_CONNECTION_STRING`.

```bash
az login
az account set --subscription "<subscription-id>"
azd auth login
azd env new "<your-alias>-sre-vm-aue"
azd env set AZURE_LOCATION australiaeast
azd up
```

Follow [Module 01](../01-deploy-and-validate/index.md) for validation.

## Use GitHub Codespaces

Codespaces can host the documentation preview, application tests, or the Azure
deployment terminal, but verify each prerequisite:

```bash
az version
azd version
python --version
node --version
dotnet --info
docker info
```

For application tests, the Codespace must expose a working Docker daemon. If it
does not, use a disposable external PostgreSQL 16 service through
`ORDERS_TEST_POSTGRES_CONNECTION_STRING`.

Authenticate for Azure deployment:

```bash
az login --use-device-code
az account set --subscription "<subscription-id>"
azd auth login
```

Forward port 8000 for MkDocs or port 8080 for a local API only. The deployed
Orders API uses its own Azure public DNS endpoint.

Codespaces can suspend an inactive environment:

* A local container and API stop when the Codespace stops.
* Testcontainers cannot complete teardown after an abrupt host termination, so
  inspect the container inventory after restart.
* Azure resources continue running and billing until
  `azd down --purge --force`.
* An Azure CPU fault expires automatically, but a PostgreSQL NSG `Deny` remains
  until reset or a reconciling `azd up`.

Always run `python scripts/workshop.py fault reset` after reconnecting and before
final evidence or cleanup.

## Use Azure Cloud Shell

Cloud Shell is suitable for Azure workshop deployment:

```bash
git clone https://github.com/charliekw411/sre-agent-workshop.git
cd sre-agent-workshop
az account show --output table
azd version
python -m pip install -r requirements.txt
azd auth login
```

Install or update azd if it is older than 1.18. Do not assume Cloud Shell offers
a Docker daemon or .NET 8 SDK for application tests. Use it for the Azure path
unless you have independently provided and validated those prerequisites.

Cloud Shell persistence depends on session configuration. Copy workshop notes
somewhere durable before cleanup or session reset.

## Helper parity

After Azure deployment:

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

Both wrappers call `scripts/workshop.py`. CPU uses VM Run Command; PostgreSQL
control uses the exact NSG child rule plus an API-only Run Command recycle to
drain stateful pooled connections. Scenario-specific reset actions remain
independent.

<div class="sre-nav" markdown>
[:material-arrow-left: Cost Management](03-cost-management.md)
[Configure the Site Azure Connection :material-arrow-right:](05-site-azure-connection.md)
</div>
