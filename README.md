---
title: Azure SRE Agent Workshop
description: A hands-on workshop that teaches incident detection, investigation, and root cause analysis on Azure using Azure SRE Agent and native Azure monitoring.
ms.date: 2026-09-23
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

A single-VM deployment of the Orders API:

* One Ubuntu 24.04 VM, `Standard_D2as_v5` by default, running the .NET 8 Orders API as a non-root systemd service
* SQLite on a separate 8 GiB managed data disk, mounted by UUID at `/var/lib/orders`
* A VNet, subnet, NSG, and static public IP with a stable Azure DNS name
* Public HTTP on port 8080 only, with no public SSH or fault-injection endpoints
* Log Analytics, Application Insights, Azure Monitor Agent, a Data Collection Rule, and CPU, disk, and request-error alerts
* A read-only Azure SRE Agent with Application Insights and Log Analytics connectors

Catalog API, Container Apps, ACR, Azure SQL, Key Vault, private endpoints, private
DNS zones, and container jobs are no longer deployed. VM administration and
bounded CPU/disk faults use authenticated Azure VM Run Command.

This is a disposable workshop workload, not a production architecture: the public
API is unauthenticated HTTP, there is no high availability or database backup,
and only synthetic data should be used. Azure policy must permit the VM public
endpoint and outbound access to Ubuntu packages, NuGet, and Azure monitoring.
No policy exemptions are created. The VM, disks, public IP, monitoring, and SRE
Agent incur charges until removed; old Container Apps cost estimates do not apply.

The curriculum in `docs/sre/` and instructional content in `agent/` have not yet
been migrated. Deployment does not upload those legacy instructions to the new
SRE Agent. Use the deployment and validation commands below for this architecture.

## Repository layout

```text
.
├── agent/              Agent instructions and investigation runbooks
├── azure.yaml          Azure Developer CLI project definition
├── docs/               Workshop content published to GitHub Pages
├── infra/              Bicep templates and the azd deployment entry point
├── scripts/            Load generation and fault injection helpers
├── src/                Orders API and application regression tests
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
* Azure CLI 2.60 or later. Deployment does not require Azure CLI extensions.
* Bash or PowerShell 7, plus OpenSSH `ssh-keygen`.
* Python 3.10 or later with PyYAML (`python -m pip install -r requirements.txt`).
* Permission to execute VM Run Command and query workspace logs for live validation.

A local Docker daemon or .NET SDK is not required for deployment. The VM installs
the Ubuntu-packaged .NET 8 SDK and builds the small, checksummed source bundle
sent through Run Command. Local application development requires the .NET 8 SDK
or a newer SDK with the .NET 8 runtime.

## Deploy the complete workshop

From the cloned repository, with prerequisites installed:

```bash
az login
az account set --subscription "<your-subscription-id>"
azd auth login
azd env list
azd env new "<your-alias>-sre-vm-aue"
azd env set AZURE_LOCATION australiaeast
azd up
```

Start with a new environment, not a prior Container Apps deployment. `preup`
checks identity, registers required providers, and generates the provisioning
public key; its unused private key is discarded. The `up` workflow provisions
Bicep resources, then `postprovision` mounts the managed disk, publishes the API,
initializes SQLite, enables systemd, configures an enabled Azure Monitor response
plan for Sev1 and Sev2 alerts in Review mode, and calls the actual public
`/orders` endpoint. The smoke check validates persisted order fields and confirms
the old HTTP fault routes return 404. A successful VM creation alone is not
success.

The final output includes the API URL, VM, data disk, workspace, and SRE Agent
resource IDs. Safe outputs are also exported to `.workshop/workshop.env` and
`.workshop/workshop.ps1`. Logs and JSON evidence are kept under the ignored
`.workshop/<environment>/` directory. Failures identify the stage and preserve
Azure diagnostics; guest deployment diagnostics are in
`/var/log/orders-deployment.log` and `journalctl -u orders-api`.

Repeated `azd up` uses the same disk and UUID mount, preserves all existing
orders, does not duplicate seed rows, and reuses an unchanged published bundle.
An existing resource group without the single-VM architecture tag is rejected
rather than migrated or deleted. Set `VM_SIZE` with `azd env set` before deployment
if the default size is unavailable in your subscription.

### Azure Free Account constraints

The initial $200 credit can fund paid Azure service tiers during the first
30 days; it does not restrict the VM to the monthly-free sizes. The default
`Standard_D2as_v5` consumes credit and is not a monthly-free VM. Preflight checks
the selected x64/Generation 2 size, subscription SKU restrictions, and regional
and VM-family quotas. It never requests a quota increase, upgrades a subscription,
removes its spending limit, or silently changes region. Free Trial subscriptions
[cannot request quota increases](https://learn.microsoft.com/en-us/azure/quotas/quickstart-increase-quota-portal).

Monthly-free `B1s` and `B2ats_v2` have only 1 GiB RAM; the on-VM build plus Azure
Monitor Agent has not been validated at that size. `B2pts_v2` is ARM64 and cannot
use this x64 deployment. B-series CPU-credit throttling can also affect CPU labs.
Prefer an available x64 size with at least 4 GiB RAM within existing quotas.

Log Analytics has a 1 GB/day ingestion safeguard and SRE Agent has a 500-AAU
active-usage limit. Neither is a total monetary cap: ingestion can overshoot its
cap and reaching it stops telemetry; SRE Agent always-on charges continue until
deletion unless its separate evaluation offer applies. Model availability varies
by subscription and region. No separately purchased Marketplace model is deployed.
See [Free Account credit rules](https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/create-free-services)
and [SRE Agent billing](https://learn.microsoft.com/en-us/azure/sre-agent/pricing-billing).
An E2E run on another subscription does not prove Free Account eligibility.

## Validate and benchmark the deployment

For a clean wall-clock benchmark, run `python scripts/workshop.py benchmark`
**instead of the first `azd up`** in a newly created `australiaeast` environment.
It refuses an existing resource group, times the entire `azd up` subprocess
through the final public smoke check, and records total and stage durations.
The target is approximately 8 to 15 minutes, not a guaranteed Azure SLA.
Provider registration, VM capacity, package downloads, and SRE Agent provisioning
can affect the result.

After deployment:

```bash
python scripts/workshop.py smoke
python scripts/workshop.py inspect
python scripts/workshop.py verify-restart
python scripts/workshop.py telemetry
azd up
python scripts/workshop.py inspect
```

`verify-restart` creates one persistent test order, restarts the VM through Azure,
and verifies a changed boot ID, the same disk UUID, automatic service recovery,
and the unchanged public order. `telemetry` waits up to ten minutes for VM
heartbeat, CPU, data-disk free space, API requests, SQLite dependencies, and
availability results in Log Analytics. Exception telemetry is recorded when
real application failures occur. The availability signal is a VM-local database
probe, not an independent external uptime monitor.

## Controlled workshop faults and cleanup

```bash
python scripts/workshop.py fault cpu 600 2
python scripts/workshop.py fault disk 90 600
python scripts/workshop.py fault status
python scripts/workshop.py fault reset
```

These commands require Azure VM Run Command permissions, not an HTTP token.
CPU pressure runs in a time-limited systemd unit. Disk pressure allocates a
dedicated ballast file only on the managed data disk, retains at least 128 MiB
for recovery, and removes that file on expiry or reset. Durations are capped at
30 minutes. The ten-minute examples are long enough for the five-minute alert
windows and monitoring ingestion; matching Sev1/Sev2 Azure Monitor alerts start
an SRE Agent investigation in Review mode. `reset` never deletes SQLite data. If
a Run Command request times out, inspect `fault status` before retrying an
injection.

The deployment remains running until explicitly removed:

```bash
azd down --purge
```

This removes the resource group, VM, managed disks, monitoring, and SRE Agent.
Deleting the resource group permanently deletes the workshop database.

## A warning about the sample application

Authenticated workshop fault scripts deliberately consume VM CPU and data-disk
space. Run them only against this disposable environment. The public API exposes
sample order operations, never destructive fault operations.

Do not copy this code into anything that serves real traffic.

## Related

This workshop is a sister project to the [Azure Container Apps .NET Workshop](https://azure.github.io/aca-dotnet-workshop/), which teaches you to build the platform. This one teaches you what to do when it misbehaves.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Screenshots are the highest-value contribution; module pages contain `<!-- SCREENSHOT: ... -->` markers showing where they belong.

## License

Licensed under the [MIT License](LICENSE).
