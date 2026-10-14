---
title: Module 04 - Deploy and Evaluate Proactive SRE Checks
description: Add a scheduled health check, watch its run, check its findings, and refresh a prepared Live Report.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - azure sre agent
  - scheduled tasks
  - live reports
  - health check
estimated_reading_time: 10
---

<ul class="sre-meta">
<li class="duration">Estimated time: 30 minutes</li>
<li>Module 04</li>
<li>Hands-on</li>
</ul>

## Overview

The first two exercises started investigations from alerts. Now add a scheduled
check that can report problems without waiting for an alert. Run it, check its
findings, and compare them with a Live Report.

This exercise introduces no new fault. Use the recovered environment from
Module 03.

## What you configure

Your facilitator has prepared the `proactive-health-check` skill, the
`health-report-investigator` custom agent, and an approved **Orders Service
Health** Live Report. You add or update the scheduled task.

The skill describes what to measure and how to report it. The task supplies
the prompt and schedule. The Live Report is a separate saved view you refresh.

## Tasks

### Task 1: Check the app and read the procedure

Refresh orders and status in the Orders GUI. Both faults should already be
reset. Keep that tab visible so requests continue while you work.

Open your SRE Agent's **Connectors** page. Its Log Analytics and Application
Insights connectors should be healthy and refer to your environment.

Read the [health-check skill](https://github.com/charliekw411/sre-agent-workshop/blob/main/agent/skills/proactive-health-check/SKILL.md).
Focus on its **Procedure**, **Status rubric**, and **Safety** sections:

* It checks the API, PostgreSQL, and VM.
* It separates the current state from earlier incidents.
* No samples means **Unknown**, not **Healthy**.
* It reports findings without changing resources.

If the skill, custom agent, connectors, or prepared report are missing, ask
your facilitator before continuing.

### Task 2: Deploy the task configuration

Open **Scheduled tasks**. If `orders-daily-health-report` exists, edit it.
Otherwise select **Create task**. Use these settings:

| Field | Value |
| --- | --- |
| Task name | `orders-daily-health-report` |
| Frequency | Custom cron |
| Cron expression, UTC | `0 21 * * 0-4` |
| Response custom agent | `health-report-investigator` |
| Agent autonomy level | Review |
| Status | On |

Paste this task prompt:

```text
Run the scheduled Orders service health check for the last 24 hours using
the proactive-health-check skill. Write every timestamp in UTC with New
Zealand time in parentheses. Return the findings in this thread and make no
changes.
```

Select **Create task** or **Save**, then reopen it and verify the settings.
Do not leave the portal's default autonomy mode selected.

The schedule runs on weekdays at 09:00 New Zealand Standard Time, or 10:00
during daylight time. You will run it now rather than wait for that schedule.
Each run uses Azure Agent Units; turn the task off when you finish.

<!-- SCREENSHOT: orders-daily-health-report configured for the assigned agent, with Review mode and its next run -->

### Task 3: Run it and watch the agent

Select **Run task now** and open the resulting thread. Watch the agent gather
records, chart them, and write its findings.

Check that the thread includes:

* The queried environment and time window
* Current API, PostgreSQL, and VM status
* Earlier CPU and PostgreSQL incidents, if they fall within the window
* Missing data and any lack of a historical baseline
* Sources for the important claims

Read the tool-call cards. Calls that read your environment must be queries,
read-only Azure commands, or charts. Decline any request to change resources,
send messages, or create issues.

!!! note "Check what the run actually used"
    The custom agent's tool list does not guarantee which tools a scheduled
    run uses. Check the calls in the thread. If the Application Insights
    query tool is unavailable, use the same records in Log Analytics.

### Task 4: Check the findings yourself

Open your Log Analytics workspace's **Logs**. Check recent requests.
P95 is the duration within which 95 percent of requests finished:

```kusto
AppRequests
| where TimeGenerated > ago(15m) and AppRoleName == "orders-api"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize Requests = sum(Samples),
    Failed = sumif(Samples, Success == false),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by Name
```

Check recent database calls:

```kusto
AppDependencies
| where TimeGenerated > ago(15m) and AppRoleName == "orders-api"
| where DependencyType == "PostgreSQL"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize Calls = sum(Samples), Failed = sumif(Samples, Success == false)
```

Compare the current status with these results, the newest CPU samples, and
the Orders GUI. Compare earlier incident claims with your saved alerts and
timestamps. The task's full-window totals cover 24 hours; do not compare those
totals with a 15-minute query.

Failures still inside the last 15 minutes can affect the reported status even
after a reset. Check the time window. A recovered incident outside that window
should not be described as active.

Save three claims as **Supported**, **Partly supported**, or **Unsupported**.
If a claim lacks evidence, ask the agent for its query and time window.

### Task 5: Refresh and check the Live Report

Open **Live Reports > Orders Service Health** and select **Reload**. Use the
report your facilitator prepared, not **+ New report**.

Check its API, PostgreSQL, database availability, and VM panels:

* Status agrees with the latest records, not just the whole-day totals.
* **No data** is not shown as **Healthy**.
* Recovered incidents are history, not a current outage.
* The time zone is labeled. New Zealand times should not carry a `+00:00`
  suffix.

If a panel disagrees with the logs, record the difference and show your
facilitator. A saved report can be wrong; do not assume its colored status
is correct.

<!-- SCREENSHOT: Prepared Orders Service Health report after Reload, with status and data for the assigned environment -->

Report authoring and tool approval are not required here. The
[optional extension](../30-appendix/08-proactive-extensions.md) keeps the setup
and authoring steps for readers with the appropriate access.

### Task 6: Compare the exercises

Use your notes to answer:

1. Which found each fault sooner: the alert or the next scheduled run?
2. What could the health check report that a single alert misses?
3. Which agent or report claim needed your correction?

Keep the thread and report available for the final evidence capture. You will
turn off the recurring task in Module 05.

## Check before moving on

* [ ] You saved and checked the scheduled-task configuration.
* [ ] You watched a run and checked its actual tool calls.
* [ ] You compared three claims with independent evidence.
* [ ] You reloaded the prepared Live Report and checked its status.

## Quick check

??? question "Does a scheduled check replace the alert rules?"
    No. It runs only on its schedule. Alerts can detect a threshold breach
    much earlier; the scheduled check adds a broader review.

??? question "Why check a report that the agent saved successfully?"
    Saving proves it ran, not that its status or calculations are correct.
    Compare the output with the underlying records.

## Next

[Reset and preserve evidence :material-arrow-right:](../06-cleanup/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 03 - Respond to PostgreSQL Connectivity Loss](../04-incident-postgresql/index.md)
[Module 05 - Reset and Preserve Evidence :material-arrow-right:](../06-cleanup/index.md)
</div>
