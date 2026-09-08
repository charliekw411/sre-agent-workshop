---
title: Azure SRE Agent Workshop
description: A hands-on workshop that teaches you to detect, investigate, and resolve production incidents on Azure using Azure SRE Agent and native Azure monitoring.
ms.date: 2026-09-08
ms.topic: overview
keywords:
  - azure sre agent
  - site reliability engineering
  - incident response
  - azure monitor
  - root cause analysis
estimated_reading_time: 4
---

<div class="sre-hero" markdown>
<h1>Azure SRE Agent Workshop</h1>
<p>Break a real application on purpose. Watch Azure Monitor light up. Then let Azure SRE Agent tell you what happened, why it happened, and what to do about it.</p>
</div>

## What you build

You deploy a small but realistic order-processing solution to Azure Container Apps, wire it to native Azure monitoring, and connect Azure SRE Agent to the resource group. Then you deliberately break it three different ways and work each incident end to end.

By the end you will have run a full incident lifecycle: detection, triage, investigation, root cause analysis, and mitigation, with an AI agent doing the tedious parts and you doing the thinking.

<div class="grid cards" markdown>

* :material-rocket-launch: **Deploy once, break repeatedly**

    One Bicep deployment gives you a Container Apps environment, Azure SQL Database, Log Analytics, Application Insights, and a full alerting stack.

* :material-fire: **Three realistic incidents**

    Runaway CPU, a cascading HTTP 500 failure, and a database that runs out of storage. All triggered on demand from fault-injection endpoints.

* :material-robot-outline: **Agent-led investigation**

    Ask Azure SRE Agent to correlate metrics, logs, traces, and deployment history, then review the diagnosis it produces.

* :material-tune: **Tune the agent**

    Write custom agent instructions and runbooks, then re-run an incident to measure whether the diagnosis actually improved.

</div>

## Objectives and outcomes

After completing this workshop, you should:

* Understand what Azure SRE Agent does, what it can access, and where a human stays in the loop.
* Deploy a multi-service Azure workload with reproducible infrastructure as code.
* Configure native Azure monitoring so that incidents are detectable without third-party tooling.
* Trigger controlled failures that mirror common production outages.
* Drive an Azure SRE Agent investigation from alert to diagnosis to mitigation proposal.
* Read and critique an AI-generated root cause analysis instead of accepting it blindly.
* Improve agent accuracy by supplying architectural context, runbooks, and custom instructions.
* Design multi-agent investigation patterns for workloads owned by more than one team.

## Learning path

The workshop is sequential. Each module assumes the resources and knowledge from the previous one.

```mermaid
flowchart TD
    M00[00 Workshop Introduction] --> M01[01 Prerequisites]
    M01 --> M02[02 Solution Architecture]
    M02 --> M03[03 Deploy Azure Infrastructure]
    M03 --> M04[04 Enable Native Azure Monitoring]
    M04 --> M05[05 Configure Azure SRE Agent]
    M05 --> M06[06 Generate High CPU Incident]
    M06 --> M07[07 Investigate the High CPU Incident]
    M07 --> M08[08 Generate HTTP 500 Incident]
    M08 --> M09[09 Investigate the HTTP 500 Incident]
    M09 --> M10[10 Generate Disk Full Incident]
    M10 --> M11[11 Perform Root Cause Analysis]
    M11 --> M12[12 Improve Agent Instructions]
    M12 --> M13[13 Multi-Agent Investigation Patterns]
    M13 --> M14[14 Cleanup]

    classDef setup fill:#dbeafe,stroke:#1d4ed8,color:#1e3a8a
    classDef incident fill:#fee2e2,stroke:#b91c1c,color:#7f1d1d
    classDef investigate fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef advanced fill:#ede9fe,stroke:#6d28d9,color:#4c1d95

    class M00,M01,M02,M03,M04,M05 setup
    class M06,M08,M10 incident
    class M07,M09,M11 investigate
    class M12,M13,M14 advanced
```

## Time and cost

| Segment                            | Modules | Estimated duration |
|------------------------------------|---------|--------------------|
| Setup and deployment               | 00 - 05 | 90 minutes         |
| Incident generation and triage     | 06 - 10 | 100 minutes        |
| Root cause analysis and tuning     | 11 - 13 | 90 minutes         |
| Cleanup                            | 14      | 10 minutes         |

Total hands-on time is roughly five hours. Running the deployed environment costs a few US dollars per day; see [Cost Management](sre/30-appendix/03-cost-management.md) for the breakdown and for guidance on scaling to zero between sessions.

!!! warning "Delete your resources when you finish"
    The workshop deliberately provisions an Azure SQL Database and an always-on Container Apps replica. Leaving them running after the workshop costs money for no benefit. [Module 14](sre/14-cleanup/index.md) removes everything in a single command.

## Who this is for

This workshop targets platform engineers, SREs, DevOps engineers, and application developers who carry a pager. You should be comfortable with the Azure portal and a terminal. Deep Kubernetes or .NET knowledge is not required, because every command you need is copy-paste ready.

## Get started

[Start the workshop :material-arrow-right:](sre/00-workshop-intro/index.md){ .md-button .md-button--primary }
[Review prerequisites](sre/01-prerequisites/index.md){ .md-button }

!!! tip "Related workshop"
    This workshop is a sister project to the [Azure Container Apps .NET Workshop](https://azure.github.io/aca-dotnet-workshop/). That workshop teaches you how to build the platform. This one teaches you what to do when the platform misbehaves at 3 a.m.
