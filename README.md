---
title: Azure SRE Agent Workshop
description: A hands-on workshop that teaches incident detection, investigation, and root cause analysis on Azure using Azure SRE Agent and native Azure monitoring.
ms.date: 2026-09-21
ms.topic: overview
keywords:
  - azure sre agent
  - site reliability engineering
  - incident response
  - azure monitor
---

# Azure SRE Agent Workshop

[![Deploy Workshop Site](https://github.com/charliekw411/sre-agent-workshop/actions/workflows/deploy-docs.yml/badge.svg)](https://github.com/charliekw411/sre-agent-workshop/actions/workflows/deploy-docs.yml)

Deploy a realistic Azure workload, break it three different ways on purpose, and use Azure SRE Agent to detect, investigate, and explain each failure. Fifteen modules, roughly five hours, no instructor required.

**[Start the workshop](https://charliekw411.github.io/sre-agent-workshop/)**

## What you learn

* Configure native Azure monitoring so incidents are detectable without third-party tooling.
* Drive an Azure SRE Agent investigation from alert to diagnosis to mitigation proposal.
* Read and critique an AI-generated root cause analysis instead of accepting it.
* Improve agent accuracy with architectural context and investigation runbooks.
* Design agent topologies for systems owned by more than one team.

## Learning path

| Module | Title                                | Duration |
|--------|--------------------------------------|----------|
| 00     | Workshop Introduction                | 40 min   |
| 01     | Prerequisites                        | 20 min   |
| 02     | Solution Architecture                | 25 min   |
| 03     | Deploy Azure Infrastructure          | 30 min   |
| 04     | Enable Native Azure Monitoring       | 30 min   |
| 05     | Configure Azure SRE Agent            | 25 min   |
| 06     | Generate High CPU Incident           | 20 min   |
| 07     | Investigate the High CPU Incident    | 30 min   |
| 08     | Generate HTTP 500 Incident           | 20 min   |
| 09     | Investigate the HTTP 500 Incident    | 35 min   |
| 10     | Generate Disk Full Incident          | 25 min   |
| 11     | Perform Root Cause Analysis          | 35 min   |
| 12     | Improve Agent Instructions           | 35 min   |
| 13     | Multi-Agent Investigation Patterns   | 30 min   |
| 14     | Cleanup                              | 10 min   |

## What gets deployed

Contoso Order Services, a two-service order-processing platform:

* `orders-api` on Azure Container Apps with public HTTPS ingress
* `catalog-api` on Azure Container Apps with internal ingress
* A Consumption workload-profile Container Apps environment, `cae-private-<suffix>`, in a delegated subnet of `vnet-<suffix>`
* Azure SQL Database with a deliberately small size ceiling and public network access disabled
* Key Vault with public network access disabled for the fault-injection secret
* Two Private Endpoints, one each for SQL and Key Vault, with VNet-linked private DNS
* A public Basic Azure Container Registry for Entra-authenticated remote builds and managed-identity image pulls, with admin and anonymous access disabled
* Log Analytics workspace and workspace-based Application Insights
* Five metric alert rules and two log alert rules
* Azure SRE Agent scoped to the workshop resource group
* A manual-trigger SQL bootstrap job, started and checked automatically by deployment hooks
* Managed-identity Container Apps jobs `workshop-token-init` and `workshop-fault-client` for private-vault initialization and fault requests

The earlier estimate of 2 to 4 US dollars per day excludes the added private-network
and job costs, as well as Azure SRE Agent. Two Private Endpoints add about 0.48 US
dollars per day at an illustrative 0.01 US dollars per endpoint-hour, plus data
processing, private DNS, and short on-demand jobs. Regional rates vary. See
[Cost Management](docs/sre/30-appendix/03-cost-management.md).

This design accommodates policies that disable SQL and Key Vault public access
and disallow storage shared keys. It does not make every endpoint private: ACR,
Azure Monitor ingestion/query, and Orders ingress remain public. Policies that
also block those paths require additional architecture work or an approved
environment, not policy-bypass tags or exemptions.

## Repository layout

```text
.
├── agent/              Agent instructions and investigation runbooks
├── azure.yaml          Azure Developer CLI project definition
├── docs/               Workshop content published to GitHub Pages
├── infra/              Bicep templates and the azd deployment entry point
├── scripts/            Load generation and fault injection helpers
├── src/                Sample .NET 8 services
├── mkdocs.yml          Site configuration
└── Makefile            Documentation build targets
```

## Running the site locally

```bash
python3 -m venv .venv
source .venv/bin/activate
make serve
```

Open [http://localhost:8000](http://localhost:8000).

## Prerequisites

* Subscription `Owner`, or `Contributor` plus `User Access Administrator`, to create the resource group and all role assignments.
* Azure Developer CLI 1.18 or later.
* Azure CLI 2.60 or later with the `containerapp`, `application-insights`, and `log-analytics` extensions.
* Bash or PowerShell.
* Python 3.10 or later with PyYAML (`python -m pip install -r requirements.txt`).
* `jq` for parsing command output.

Full details in [Module 01](docs/sre/01-prerequisites/index.md).

## Deploy the complete workshop

From the cloned repository, with prerequisites installed:

```bash
az login
az account set --subscription "<your-subscription-id>"
azd auth login
azd env new "<your-alias>-workshop"
azd env set AZURE_LOCATION eastus2
azd up
```

`azd up` runs `provision`, then `package`, then `deploy --all`. Provisioning creates
the private networking, apps, monitoring, SRE Agent, RBAC, and Key Vault with faults
initially disabled. The `postprovision` hook starts `workshop-token-init`, waits
for success, attaches the private Key Vault reference to `orders-api`, and enables
fault endpoints. The job creates only a missing `fault-token`, preserving an
existing token. No ARM deployment script, supporting storage account, or Azure
Container Instance is needed.

Packaging builds images remotely in ACR. After deployment, `postdeploy` runs the
managed-identity SQL bootstrap, makes a smoke request, then syncs
`agent/incident-filters.yaml` and `agent/knowledge.yaml` and waits for indexing.
No local Docker or .NET SDK, portal configuration, manual role grants, SQL
passwords, or pasted agent configuration are required.
SRE Agent uses `Microsoft.App/agents@2025-05-01-preview`; region availability is
constrained, so use the supported default `eastus2`.

SQL is Entra-only, with a separate bootstrap identity and object-level runtime
permissions. Initialization inserts five deterministic seed orders without
overwriting existing orders or ballast; see [Module 03](docs/sre/03-deploy-infrastructure/index.md).
The runtime agent investigates read-only; it cannot start the jobs or read vault
secrets and is not granted the subscription `Monitoring Contributor` role needed
for full Azure Monitor alert lifecycle operations.

After deployment, load `source .workshop/workshop.env` in Bash or
`. ./.workshop/workshop.ps1` in PowerShell. These allowlisted exports contain only
safe identifiers and endpoints, not secrets. Use `./scripts/inject-fault.sh status`
or `./scripts/inject-fault.ps1 status`. The same helper commands and defaults now
start and wait for `workshop-fault-client` through ARM. That job retrieves the
credential inside the VNet and calls the existing `/fault` routes; your laptop
never retrieves it and needs no VPN or private-vault data access.

The helper queries only the correlated non-secret JSON result in
`ContainerAppConsoleLogs_CL`, using the Azure CLI `log-analytics` extension.
Your subscription role must permit starting the job and querying workspace logs.
Progress goes to stderr and JSON to stdout. Log ingestion can take up to five
minutes after job success; status is a snapshot from the job, not necessarily
the state when you receive it. On a result timeout, retain the execution name and
32-character request ID and run `python scripts/workshop.py fault-result <request-id>`
to retry read-only retrieval, never reinject to fetch a missing result. Queries
cover the last hour (`PT1H`), subject to log availability policies. See
[fault helper results](docs/sre/30-appendix/01-variables.md#fault-helper-results-and-retry).

An earlier failed deployment with no apps or jobs can rerun in the same azd environment,
creating `cae-private-*` and reusing existing SQL, vault, and data. If apps or jobs already
use the old non-VNet environment, preprovision stops before attempting a move or
deletion; choose a new azd environment name. See
[migration guidance](docs/sre/03-deploy-infrastructure/index.md#updating-an-earlier-deployment)
before retrying. Updating these files alone changes no Azure resources.

To refresh checked-in agent content without a full deployment, run
`python scripts/workshop.py configure-agent`.

## A warning about the sample application

`orders-api` includes fault-injection endpoints that saturate CPU, force dependency failures, and consume database storage until writes fail. They are gated behind a shared secret and disabled unless explicitly enabled.

Do not copy this code into anything that serves real traffic.

## Related

This workshop is a sister project to the [Azure Container Apps .NET Workshop](https://azure.github.io/aca-dotnet-workshop/), which teaches you to build the platform. This one teaches you what to do when it misbehaves.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Screenshots are the highest-value contribution; module pages contain `<!-- SCREENSHOT: ... -->` markers showing where they belong.

## License

Licensed under the [MIT License](LICENSE).
