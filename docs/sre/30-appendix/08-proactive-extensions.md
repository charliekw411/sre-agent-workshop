---
title: Health-check Setup and Report Authoring
description: Install the supplied health-check components and author a Live Report when you have configuration and approval access.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - scheduled tasks
  - live reports
  - azure sre agent
estimated_reading_time: 8
---

The core [proactive exercise](../05-scheduled-health-check/index.md) uses
prepared components and a report. Use this guide as an organizer or independent
environment owner. Participants use the prepared configuration; authoring their
own report remains an optional extension.

## Install the supplied components

The definitions from Igor's
[#33](https://github.com/charliekw411/sre-agent-workshop/pull/33) are:

| Component | Definition |
| --- | --- |
| `proactive-health-check` skill | [SKILL.md](https://github.com/charliekw411/sre-agent-workshop/blob/main/agent/skills/proactive-health-check/SKILL.md) |
| `health-report-investigator` custom agent | [Task YAML](https://github.com/charliekw411/sre-agent-workshop/blob/main/agent/scheduled-tasks/orders-daily-health-report.yaml) |
| `orders-daily-health-report` scheduled task | The same YAML |

Review the read-only procedure before installation. The configured tools
query monitoring, read Azure state, and chart results. The installer does not
attach Azure write, email, or issue-creation tools.

Use a [prepared terminal](01-variables.md#terminal-setup-for-an-assigned-environment)
with access to configure the agent. Agent-scoped Administrator is the
facilitator/deployer path used for the complete setup and report approval.
Standard User alone cannot install the custom agent.

=== "Bash"

    ```bash
    python scripts/workshop.py health-check install
    python scripts/workshop.py health-check status
    ```

=== "PowerShell"

    ```powershell
    python .\scripts\workshop.py health-check install
    python .\scripts\workshop.py health-check status
    ```

The installer writes the skill, custom agent, and task, then reads them back.
Expect `installed` and `matchesDefinition` to be `true`. It checks the prompt,
skill text, tools, schedule, enabled state, and Review mode. It changes no VM,
database, connector, or response plan.

Repeating installation reconciles the same three names. Evidence is saved
under `.workshop/<environment>/scheduled-health-check.json`. Missing or
unhealthy monitoring connectors must be fixed before installation succeeds.

If you edit the task in the portal, `matchesDefinition` can become `false`,
including when you turn it off. Inspect the reported differences; do not
automatically overwrite a deliberate edit.

For class preparation, turn the installed task off until Module 04. It is a
required post-provisioning step for the class; infrastructure deployment does
not install it.

## Schedule and run safety

The default `0 21 * * 0-4` is weekdays at 09:00 NZST (UTC+12), or 10:00 NZDT
(UTC+13). For 09:00 NZDT, an owner can change the YAML to `0 20 * * 0-4` and
reinstall. The agent evaluates cron in UTC.

In testing, scheduled runs did not use the custom agent's instructions or tool
list. Read-only rules also live in the skill and task prompt. Check the actual
run's tool calls and keep Review mode and read-only Azure permissions.

The optional Application Insights query tool was unavailable in those runs.
The skill uses the same data in the Log Analytics `App*` tables and reports
that limitation. It must not invent a cross-check or a baseline.

## Author a Live Report

Creating and reloading reports requires an appropriate SRE Agent user role.
Approving tools and deleting reports require Administrator. For a participant
extension, arrange facilitator approval in advance rather than self-elevating.
Keep recent Orders traffic running.

In **Live Reports**, first check whether **Orders Service Health** already
exists. To change it, use **Open authoring thread**. Use **+ New report** only
when creating your own first report.

Paste this request in the authoring thread:

```text
Build a Live Report called "Orders Service Health" from the agent's
connected Log Analytics workspace and Application Insights data. Cover the
last 24 hours with a time-range selector. Show New Zealand time
(Pacific/Auckland) computed with datetime_utc_to_local as plain text such as
2026-10-13 10:47, without a UTC offset suffix. Label the zone and keep UTC
available in a tooltip or secondary column.

Include:
1. Orders API request volume, success rate, and P95 duration by operation
   from AppRequests where AppRoleName is "orders-api". Show GET /health/live
   separately from the database-backed operations.
2. PostgreSQL dependency calls, failure percentage, and P95 duration from
   AppDependencies where DependencyType is "PostgreSQL".
3. Database availability results from AppAvailabilityResults where Name is
   "orders-api-postgresql".
4. VM heartbeat continuity from Heartbeat and guest CPU from Perf where
   CounterName is "% Processor Time".
5. A status indicator for each area, computed only from the last 15 minutes
   and independent of the selected window:
   - No data: no samples in the last 15 minutes. Never show Healthy
     without samples.
   - Unhealthy: any failed request, PostgreSQL dependency call, or
     availability check; average guest CPU above 80 percent; or a
     heartbeat gap longer than 5 minutes.
   - Degraded: no failures, but P95 duration above 1,000 ms.
   - Healthy: samples exist and none of the above applies.
   Show the selected-window numbers beside each status. A failure earlier
   in the window that has recovered is a window finding, not the current
   status.
6. A short list of missing data.

Use read-only queries only. Do not add model-assisted analysis, buttons, or
any tool that changes Azure resources, sends email, or creates issues.
```

An authorized Administrator reviews and approves the report's tools. In
testing, one approval was needed for the read-only `system-mcp-monitor`
workspace query tool. Decline writes, messages, or issue creation.

Wait for the saved version, open the report, and select **Reload**.
Check each status against its table and the
[Module 04 queries](../05-scheduled-health-check/index.md#task-4-check-the-findings-yourself).
For VM and availability detail, use the skill's reference queries and the
workspace's `AppAvailabilityResults` records.

A generated report can show the wrong status even when its table has data.
It can also mislabel local times. Report the specific defect in the authoring
thread, ask the agent to test the change, and check the new version. Do not
create another report to fix the existing one.

Live Reports is a preview feature. If it is unavailable, record the limitation
and keep the log queries as your view; do not report the exercise as completed.

## Remove the optional components

Save the report snapshot and task findings first. Then an authorized owner can
run:

=== "Bash"

    ```bash
    python scripts/workshop.py health-check remove
    python scripts/workshop.py health-check status
    ```

=== "PowerShell"

    ```powershell
    python .\scripts\workshop.py health-check remove
    python .\scripts\workshop.py health-check status
    ```

Expect `installed` to be `false`. Removal deletes only the skill, custom agent,
and task, not the response plan, connectors, or Live Report. Delete an unwanted
report separately with Administrator access.

The core cleanup only turns off the task and preserves history for review.
Neither path deletes Azure infrastructure or stops all resource charges.

<div class="sre-nav" markdown>
[Module 04 - Deploy and Evaluate Proactive SRE Checks](../05-scheduled-health-check/index.md)
[Workshop home :material-home:](../../index.md)
</div>
