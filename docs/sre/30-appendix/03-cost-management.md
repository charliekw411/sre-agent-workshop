---
title: Cost Management
description: Know what continues to cost money and who stops workshop activity and deletes the resources.
ms.date: 2026-10-14
ms.topic: concept
keywords:
  - azure costs
  - scheduled tasks
  - workshop cleanup
estimated_reading_time: 4
---

## What participants need to know

Resetting an incident restores the app. It does not stop Azure charges.
Closing the browser does not stop the VM, database, or scheduled task.

In [Module 05](../06-cleanup/index.md), reset both faults, turn off your
recurring task, and save your evidence. Leave resource deletion to your
facilitator.

## What costs money

| Resource | Cost continues while |
| --- | --- |
| VM and its storage | The VM is allocated; storage remains even when it is stopped |
| PostgreSQL | Compute, provisioned storage, and backups remain |
| Public IP and private DNS | The resources remain |
| Monitoring and alerts | Data is ingested/retained and rules are evaluated |
| SRE Agent | The agent exists and agent operations run, according to the current offer |
| Scheduled health check | The enabled task keeps running |
| Live Report | You create/update it, or add model-generated analysis on refresh |

Prices depend on region, subscription, agreement, and usage. Use the
[Azure pricing calculator](https://azure.microsoft.com/pricing/calculator/)
and actual Cost Management records, not a fixed workshop quote.

The lab uses one VM and a small, private PostgreSQL server without high
availability. These save cost at the expense of resilience. They are not
production recommendations.

## Health-check and report usage

Each scheduled run and report creation/update consumes Azure Agent Units.
Reloading a report that only queries connector data does not consume
active-flow units. Model-generated analysis on each refresh does, so the
workshop report excludes it.

Use the prepared report in the core module. If you author one in the optional
exercise, update the existing report rather than creating copies.

Check **Settings > Agent consumption** in the SRE Agent portal. Usage records
can lag. The configured 500-unit monthly limit and Log Analytics 1 GB/day cap
are safeguards, not a complete spending limit.

## Organizer cost controls

Before class, set a budget and notifications for the workshop scopes.
Include the subscription owner. A budget notifies; it does not stop or
delete resources.

Review **Cost Management > Cost analysis**, filtered to the relevant resource
groups. Participants may not have billing access. Records can arrive after
the workshop or after deletion.

Do not deallocate participant VMs while exercises or alert collection are
running. Stopping the VM alone leaves database, storage, monitoring, IP,
and agent costs.

After participants finish, follow
[organizer cleanup](07-organizer-guide.md#after-the-workshop). For your own
independent deployment, use
[self-deployment cleanup](06-self-deployment.md#delete-your-own-environment).
Verify deletion; an unreachable Orders URL is not enough.

<div class="sre-nav" markdown>
[:material-arrow-left: Troubleshooting](02-troubleshooting.md)
[Running Locally or in Codespaces :material-arrow-right:](04-local-and-codespaces.md)
</div>
