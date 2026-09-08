---
title: Azure SRE Agent Workshop
description: A hands-on workshop that teaches incident detection, investigation, and root cause analysis on Azure using Azure SRE Agent and native Azure monitoring.
ms.date: 2026-09-08
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

* `orders-api` on Azure Container Apps with public ingress
* `catalog-api` on Azure Container Apps, internal only
* Azure SQL Database with a deliberately small size ceiling
* Log Analytics workspace and workspace-based Application Insights
* Five metric alert rules and two log alert rules
* Azure SRE Agent scoped to the workshop resource group

Estimated cost is 2 to 4 US dollars for a single-day run. See [Cost Management](docs/sre/30-appendix/03-cost-management.md).

## Repository layout

```text
.
├── agent/              Agent instructions and investigation runbooks
├── docs/               Workshop content published to GitHub Pages
├── infra/              Bicep templates: foundation, apps, alerts
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

* An Azure subscription where you can create resources and assign roles. Contributor alone is not sufficient.
* Azure CLI 2.60 or later with the `containerapp`, `application-insights`, and `log-analytics` extensions.
* Bash, available through WSL, macOS, Linux, Git Bash, or Azure Cloud Shell.
* `jq` for parsing command output.

Full details in [Module 01](docs/sre/01-prerequisites/index.md).

## A warning about the sample application

`orders-api` includes fault-injection endpoints that saturate CPU, force dependency failures, and consume database storage until writes fail. They are gated behind a shared secret and disabled unless explicitly enabled.

Do not copy this code into anything that serves real traffic.

## Related

This workshop is a sister project to the [Azure Container Apps .NET Workshop](https://azure.github.io/aca-dotnet-workshop/), which teaches you to build the platform. This one teaches you what to do when it misbehaves.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Screenshots are the highest-value contribution; module pages contain `<!-- SCREENSHOT: ... -->` markers showing where they belong.

## License

Licensed under the [MIT License](LICENSE).
