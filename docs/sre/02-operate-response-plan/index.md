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

The existing `workshop-sev1-sev2-review` plan sends Azure Monitor alerts to the
agent. Edit this plan rather than adding a second plan with the same filters.

| Setting | Workshop value |
| --- | --- |
| Incident platform | Azure Monitor |
| Severity | Sev1 and Sev2 |
| Handling agent | Coordinating or main agent |
| Autonomy level | Review |
| Status | On |
| Merge/reinvestigation window | 3 hours |
| Maximum investigation attempts | 3 |

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
for reading resources and monitoring data.

| Operation | SRE Agent role needed |
| --- | --- |
| Chat and manage scheduled tasks | Standard User |
| Edit response plans and create custom agents | Author |
| Approve Live Report tools or delete a report | Administrator |

For the core exercises, your facilitator must arrange Standard User and
Author access on your own agent, or an equivalent agent-scoped Administrator
assignment. Azure Contributor alone does not replace these SRE Agent roles.
Ask your facilitator if an operation is denied; do not assign yourself roles.

The workshop agent is configured with resource and Log Analytics read access.
**Review** mode asks for approval of Azure changes, but it is not a substitute
for permissions or a read-only procedure. Do not approve resource changes in
these exercises. You reset incidents through the workshop controls.

See the official [SRE Agent role reference](https://learn.microsoft.com/azure/sre-agent/user-roles)
for the full permission table.

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
* [Optional health-check setup and Live Report authoring](../30-appendix/08-proactive-extensions.md)

<div class="sre-nav" markdown>
[Module 02 - Configure SRE and Respond to High CPU](../03-incident-high-cpu/index.md)
[Workshop home :material-home:](../../index.md)
</div>
