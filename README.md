---
title: Azure SRE Agent Workshop
description: A hands-on workshop for investigating VM saturation and private PostgreSQL connectivity incidents with Azure SRE Agent.
ms.date: 2026-10-06
ms.topic: overview
keywords:
  - azure sre agent
  - site reliability engineering
  - postgresql
  - incident response
---

# Azure SRE Agent Workshop

[![Deploy Workshop Site](https://github.com/charliekw411/sre-agent-workshop/actions/workflows/deploy-docs.yml/badge.svg)](https://github.com/charliekw411/sre-agent-workshop/actions/workflows/deploy-docs.yml)

Deploy a small but realistic Azure workload, establish an evidence baseline,
trigger two controlled incidents, and use Azure SRE Agent to investigate the
resulting alerts. The six-module path takes about three and a half hours and does
not require an instructor.

**[Start the workshop](https://charliekw411.github.io/sre-agent-workshop/)**

## What you learn

* Validate an application, its private database dependency, and its telemetry
  before declaring deployment success.
* Follow Azure Monitor alerts into an Azure SRE Agent response-plan
  investigation.
* Correlate customer behavior, VM metrics, PostgreSQL dependencies, availability
  results, and Azure control-plane changes.
* Distinguish observed evidence from inference in an AI-generated investigation.
* Turn verified incident evidence into monitoring and response improvements.

## Learning path

| Module | Title | Duration |
| --- | --- | --- |
| 01 | Deploy and Validate the Workshop | 45 min |
| 02 | Operate the SRE Agent Response Plan | 25 min |
| 03 | Respond to High CPU | 40 min |
| 04 | Respond to PostgreSQL Connectivity Loss | 40 min |
| 05 | Review and Improve the Response | 40 min |
| 06 | Preserve Evidence and Clean Up | 20 min |

## What gets deployed

The workshop uses one Azure region and one disposable resource group:

* One Ubuntu 24.04 VM, `Standard_D2as_v5` by default, running the public .NET 8
  Orders API and browser GUI as a non-root `systemd` service.
* A private Azure Database for PostgreSQL Flexible Server 16 using
  `Standard_B1ms`, 32 GiB storage with auto-grow, no high availability, and
  seven-day locally redundant backups.
* An Orders subnet at `10.240.0.0/27` and a PostgreSQL delegated subnet at
  `10.240.0.32/27`.
* The private DNS zone `private.postgres.database.azure.com`; PostgreSQL public
  access is disabled.
* A static public IP and Azure DNS name for HTTP port 8080. SSH and public
  fault-injection endpoints are not exposed.
* Log Analytics, workspace-based Application Insights, Azure Monitor Agent, a
  Data Collection Rule, and CPU, PostgreSQL dependency, and HTTP 5xx alerts.
* A read-only Azure SRE Agent with an enabled Sev1/Sev2 response plan in Review
  mode.

The VM's system-assigned managed identity obtains Microsoft Entra tokens for
PostgreSQL and connects with TLS `VerifyFull`. The same identity is also the
PostgreSQL Entra administrator. That administrator/runtime combination is an
intentional privilege shortcut for a disposable workshop. A production design
should use separate provisioning and runtime identities and grant the runtime
identity only the database privileges the application needs.

### Why a private managed database

Keeping PostgreSQL on the application VM would be cheaper, but it would couple
database and VM lifecycle, put both components in one failure domain, and remove
the network dependency that Module 04 investigates. A public managed database
would retain managed operations but expose a public endpoint and rely on public
firewall configuration. The private Flexible Server gives repeatable
provisioning, independent database lifecycle, private name resolution, and a
realistic dependency boundary without database passwords.

This remains a cost-conscious workshop rather than a production reference
architecture. The API has one VM, the database has no HA, and both are in one
region. The separate managed service improves isolation, backup behavior, and
database lifecycle, but it does not remove those deliberate failure-domain
tradeoffs.

## Repository layout

```text
.
├── azure.yaml          Azure Developer CLI project definition
├── docs/               Workshop content published to GitHub Pages
├── infra/              Bicep templates and the azd deployment entry point
├── scripts/            Validation, load, and controlled-fault helpers
├── src/                Orders API, browser GUI, and application tests
├── web/                Authenticated documentation-site controls
├── mkdocs.yml          Site configuration
└── Makefile            Documentation build targets
```

## Prerequisites

* Subscription `Owner`, or `Contributor` plus `User Access Administrator`, to
  create the resource group and deployment-managed role assignments.
* Azure Developer CLI 1.18 or later.
* Azure CLI 2.60 or later. No Azure CLI extension is required.
* Python 3.10 or later with the packages in `requirements.txt`.
* Bash with `curl` and `jq`, or PowerShell 7, plus OpenSSH `ssh-keygen`.
* Node.js 20 or later when building the documentation site's authenticated
  controls.
* Permission to query the workspace and execute VM Run Command for live
  validation.

Azure deployment does not require a local .NET SDK or Docker. Application tests
do require the .NET 8 SDK and a real PostgreSQL 16 instance, normally started by
Testcontainers through a running Docker daemon. See
[Running Locally or in Codespaces](docs/sre/30-appendix/04-local-and-codespaces.md).

## Deploy the workshop

From a cloned repository:

```bash
az login
az account set --subscription "<your-subscription-id>"
azd auth login
azd env new "<your-alias>-sre-vm-aue"
azd env set AZURE_LOCATION australiaeast
azd up
```

Use a new environment. Preflight checks that Azure CLI and azd use the same
principal and tenant, registers required providers, verifies the selected VM
capacity, and verifies that PostgreSQL 16 with `Standard_B1ms` is advertised in
the region. It does not change region, request quota, create policy exemptions,
or substitute another SKU.

The resource group is tagged
`workshop-architecture=single-vm-postgresql-v1`. A resource group with another
architecture tag is rejected rather than migrated, partially reused, or
deleted.

`azd up` then:

1. Provisions the VM, two subnets, private DNS, PostgreSQL, monitoring, alerts,
   and SRE Agent.
2. Makes the VM identity the PostgreSQL Entra administrator.
3. Installs the .NET 8 SDK and PostgreSQL client on the VM.
4. Validates private DNS, TCP 5432, managed-identity token acquisition, and TLS
   configuration.
5. Applies the PostgreSQL schema and deterministic seed data.
6. Enables `orders-api.service` and verifies local and public health.
7. Configures and reads back the SRE Agent response plan.
8. Verifies `/orders`, `/database`, and the absence of public `/fault/*` routes.

Repeated `azd up` reconciles the normal
`PostgreSqlFaultInjection` rule to `Allow`, reapplies the idempotent schema
bootstrap, and keeps existing orders. The final output includes resource names,
IDs, and the public API URL. The generated `.workshop/workshop.env` and
`.workshop/workshop.ps1` files use an explicit non-secret allowlist. They do not
contain a PostgreSQL password or access token.

## Use and validate the workload

Load the generated values and run the supported checks:

```bash
source .workshop/workshop.env
python scripts/workshop.py smoke
python scripts/workshop.py inspect
python scripts/workshop.py verify-restart
python scripts/workshop.py telemetry
```

Open `SERVICE_ORDERS_API_ENDPOINT_URL` to list, create, inspect, and update
synthetic orders. The status cards call:

* `/health/live` for process liveness
* `/health/ready` for schema-backed readiness
* `/database` for `provider`, `status`, `serverVersion`, `schemaVersion`, and
  `databaseBytes`

The GUI refreshes those three status routes every 30 seconds while its tab is
visible.

`inspect` verifies the non-root service, private PostgreSQL DNS resolution, TLS
`verify-full`, PostgreSQL 16, schema migration, deterministic seed rows, and
local health. `verify-restart` binds its persistence witness to
`POSTGRESQL_SERVER_RESOURCE_ID`, restarts only the VM, and confirms that the
same order remains in the separately managed database.

`telemetry` waits up to ten minutes for VM heartbeat and CPU data, Orders API
requests, PostgreSQL dependencies, and availability results. PostgreSQL
operations appear in `AppDependencies` with
`DependencyType == "PostgreSQL"`.

## Controlled incidents

The normal outbound NSG child rule is:

| Property | Value |
| --- | --- |
| Name | `PostgreSqlFaultInjection` |
| Priority | `100` |
| Direction | `Outbound` |
| Protocol | `Tcp` |
| Source | `10.240.0.0/27` |
| Destination | `10.240.0.32/27` |
| Destination port | `5432` |
| Normal access | `Allow` |

Module 04 changes only this rule from `Allow` to `Deny`. It does not modify
orders, stop the database server, expose PostgreSQL publicly, or interrupt
unrelated outbound traffic. Because NSGs preserve established flows, the
control then restarts only `orders-api` to drain its Npgsql pool and retries
until liveness is HTTP 200 while readiness and `/orders` return the controlled
database-unavailable HTTP 503. Reset restores and reads back `Allow`, restarts
only the API, and requires liveness, readiness, and `/orders` to recover before
reporting success. A later `azd up` also reconciles `Allow`.

Terminal controls are:

```bash
python scripts/workshop.py fault cpu 600 2
python scripts/workshop.py fault postgresql
python scripts/workshop.py fault status
python scripts/workshop.py fault reset-cpu
python scripts/workshop.py fault reset-postgresql
python scripts/workshop.py fault reset  # Explicitly reset all scenarios
```

The CPU incident uses authenticated VM Run Command. The PostgreSQL incident uses
an authenticated ARM deployment of the fixed NSG child rule followed by VM Run
Command to recycle only `orders-api` and verify the application data plane. The
browser launcher uses `data-sre-incident="postgresql"` and performs an
authenticated rule GET, conditional PUT, read-back, API recycle, and bounded
health/order verification. An NSG read-back alone is never reported as a
successful inject or reset.

The PostgreSQL connectivity alert is
`alert-orders-postgresql-connectivity`; it fires on any failed PostgreSQL
dependency in five minutes. Existing CPU and HTTP 5xx alerts remain enabled.

Browser participants need read access to discover the environment and query
telemetry, VM Run Command permission for CPU and API-recycle actions, and read/write
permission on the specific `PostgreSqlFaultInjection` security rule for the
PostgreSQL action. The deployment does not create participant assignments.
Prefer a custom role at the narrowest supported scope rather than broad VM or
network contributor access. See
[Configure the Site Azure Connection](docs/sre/30-appendix/05-site-azure-connection.md).

## Cost and cleanup

The VM, PostgreSQL Flexible Server, private DNS and network resources,
monitoring, public IP, alerts, and SRE Agent can all incur charges. Resetting an
incident restores connectivity but does not stop billing.

Scenario-specific resets do not change the other fault. Before collecting final
healthy evidence or deleting the environment, run the explicitly global
`python scripts/workshop.py fault reset`. When finished:

```bash
azd down --purge --force
```

This removes the workshop resource group, including the VM, private network and
DNS resources, PostgreSQL server and backups, monitoring, alerts, and SRE Agent.
Deletion is permanent. Use only synthetic data.

## Run the documentation site locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
npm ci
npm run build:web
python -m mkdocs serve
```

Open
[http://localhost:8000/sre-agent-workshop/](http://localhost:8000/sre-agent-workshop/).
Without tenant and SPA client configuration, the documentation remains readable
and Azure controls fail closed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Do not include subscription IDs, tenant
IDs, resource IDs, tokens, connection strings, or real customer data in
documentation or screenshots.

## License

Licensed under the [MIT License](LICENSE).
