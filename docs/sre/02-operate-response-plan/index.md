---
title: SRE Configuration Reference
description: Settings, permissions, and monitoring used by the reactive and proactive workshop exercises.
ms.date: 2026-10-14
ms.topic: reference
keywords:
  - azure sre agent
  - response plan
  - scheduled tasks
estimated_reading_time: 5
---

This is an optional reference. Start the hands-on configuration work in
[Module 02](../03-incident-high-cpu/index.md).

## Response plan

The existing `workshop-sev1-sev2-review` plan sends CPU and HTTP 5xx alerts to
the coordinating agent. Edit this plan rather than adding another plan with
the same filters.

| Setting | Workshop value |
| --- | --- |
| Incident platform | Azure Monitor |
| Severity | Sev1 and Sev2 |
| Handling agent | Coordinating or main agent |
| Autonomy level | Review |
| Status | On |
| Merge/reinvestigation window | 3 hours |
| Maximum investigation attempts | 3 |

The facilitator also installs a complementary plan for the PostgreSQL
exercise:

| Plan | Title contains | Handling agent |
| --- | --- | --- |
| `workshop-sev1-sev2-review` | `alert-orders-h` | Coordinating or main agent |
| `orders-incident-workflow` | `postgresql` | `orders-alert-investigator` |

Both plans stay On for Sev1 and Sev2 in Review mode. Keep the two scalar title
filters: they ensure one alert matches only one plan.

An alert can update an existing investigation instead of starting a new
thread. Match the alert, resource, and timestamps before deciding that an
investigation is missing.

## Alert rules

Find these enabled rules in your assigned resource group:

| Rule | Severity | Condition |
| --- | --- | --- |
| `alert-orders-high-cpu` | Sev2 | Average VM CPU above 80 percent for 5 minutes |
| `alert-orders-postgresql-connectivity` | Sev1 | Any failed PostgreSQL dependency in 5 minutes |
| `alert-orders-http-5xx` | Sev1 | More than 10 HTTP 5xx responses in 5 minutes |

The database fault can trigger both the dependency and HTTP 5xx alerts.
They show the failed connection and the customer impact, respectively.

## Your access and the agent's access

Your account runs the fault controls. The agent has separate Azure identities
and scoped roles for resources and monitoring data.

| Operation | SRE Agent role needed |
| --- | --- |
| Chat and manage scheduled tasks | Standard User |
| Edit response plans and create custom agents | Author |
| Approve Live Report tools or delete a report | Administrator |

For the core exercises, the cohort gives you SRE Agent Administrator on your own
agent only, which covers chat, response plans, scheduled tasks and approving
Review-mode actions. Azure Contributor alone does not replace these SRE Agent
roles. Ask your facilitator if an operation is denied; do not assign yourself
roles.

The workshop agent UAMI has Reader and Contributor on its own resource group,
Log Analytics Reader on the workspace, and the product-required Monitoring
Contributor role at subscription scope. The agent's managed-resource scope
remains its workshop resource group.

**Review** mode asks for approval before Azure changes. Your Administrator role
can approve them, and the agent then acts with its own Contributor access on
your resource group, so approve nothing in these exercises unless your
facilitator tells you to. You reset incidents through the workshop controls.

See the official [SRE Agent role reference](https://learn.microsoft.com/azure/sre-agent/user-roles)
for the full permission table.

## Incident workflow configuration

[Module 03](../04-incident-postgresql/index.md) uses the configuration added in
[#35](https://github.com/charliekw411/sre-agent-workshop/pull/35):

| Component | Name | Purpose |
| --- | --- | --- |
| Response plan | `orders-incident-workflow` | Routes the PostgreSQL connectivity alert |
| Custom agent | `orders-alert-investigator` | Runs the evidence-backed RCA in Review mode |
| Skill | `orders-azure-monitor-rca` | Defines the investigation and validation procedure |
| Skills | `orders-github-issue-followup`, `orders-email-incident-followup` | Produce optional, separately approved follow-up drafts |

The classroom setup leaves GitHub and Outlook destinations unbound. The agent
can investigate with read-only Azure and telemetry tools, while issue and email
follow-ups remain draft-only.

## Proactive configuration

[Module 04](../05-scheduled-health-check/index.md) uses the definitions added in
[#33](https://github.com/charliekw411/sre-agent-workshop/pull/33):

| Component | Name | Purpose |
| --- | --- | --- |
| Skill | `proactive-health-check` | The read-only measurement and reporting procedure |
| Custom agent | `health-report-investigator` | Instructions and configured read-only tools |
| Scheduled task | `orders-daily-health-report` | Schedule, prompt, and Review mode |
| Live Report | `Orders Service Health` | A saved view you refresh and check against the logs |

The task uses `0 21 * * 0-4`, evaluated in UTC. This is weekdays at 09:00
New Zealand Standard Time, or 10:00 during daylight time. UTC is the reference
for comparing incidents; reports also show New Zealand time.

Scheduled runs did not use the custom agent's tool list in testing. Check
the actual tool calls, not just the configuration. The runs used the Log
Analytics `App*` tables when the Application Insights query tool was unavailable.

## More detail

* [Terminal setup and environment values](../30-appendix/01-variables.md)
* [Troubleshooting](../30-appendix/02-troubleshooting.md)
* [Incident workflow setup and routing](../30-appendix/09-incident-workflow.md)
* [Health-check setup and Live Report authoring](../30-appendix/08-proactive-extensions.md)

<div class="sre-nav" markdown>
[Module 02 - Configure SRE and Respond to High CPU](../03-incident-high-cpu/index.md)
[Workshop home :material-home:](../../index.md)
</div>
