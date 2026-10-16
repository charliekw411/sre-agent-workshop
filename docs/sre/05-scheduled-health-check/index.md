---
title: Module 04 - Deploy and Evaluate Proactive SRE Checks
description: Add a scheduled health check, watch its run, check its findings, and generate and check a Live Report.
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - azure sre agent
  - scheduled tasks
  - live reports
  - health check
estimated_reading_time: 12
---

<ul class="sre-meta">
<li class="duration">Estimated time: 45 minutes</li>
<li>Module 04</li>
<li>Hands-on</li>
</ul>

## Learning objective

By the end, you can set up a scheduled SRE health check, run it, verify its
findings against independent evidence, generate a Live Report from a prompt,
and use the agent to compare incidents.

## How we'll achieve it

The first two exercises started investigations from alerts. Now you add a
scheduled check that can report problems without waiting for an alert, run it,
and check its findings. You then generate a Live Report from the same
telemetry and compare it with the logs. This exercise introduces no new fault.
Use the recovered environment from Module 03.

### What you configure

Your facilitator has prepared the `proactive-health-check` skill and the
`health-report-investigator` custom agent. You add or update the scheduled task
and generate the **Orders Service Health** Live Report yourself. The
[setup guide](../30-appendix/08-proactive-extensions.md) describes how
facilitators install the prepared components.

The skill describes what to measure and how to report it. The task supplies
the prompt and schedule. The Live Report is a separate saved view that you
create from a prompt and then reload.

## Tasks

### Task 1: Check the app and read the procedure

1. Refresh orders and status in the Orders GUI. Both faults should already be
   reset. Keep that tab visible so requests continue while you work.
2. Open your SRE Agent's **Connectors** page. Its Log Analytics and Application
   Insights connectors should be healthy and refer to your environment.
3. Read the [health-check skill](https://github.com/charliekw411/sre-agent-workshop/blob/main/agent/skills/proactive-health-check/SKILL.md).
   Focus on its **Procedure**, **Status rubric**, and **Safety** sections:
    * It checks the API, PostgreSQL, and VM.
    * It separates the current state from earlier incidents.
    * No samples means **Unknown**, not **Healthy**.
    * It reports findings without changing resources.
4. If the skill, custom agent, or connectors are missing, ask your facilitator
   before continuing.

### Task 2: Deploy the task configuration

1. Open **Scheduled tasks**. If `orders-daily-health-report` exists, edit it.
   Otherwise select **Create task**.
2. Use these settings:

    | Field | Value |
    | --- | --- |
    | Task name | `orders-daily-health-report` |
    | Frequency | Custom cron |
    | Cron expression, UTC | `0 21 * * 0-4` |
    | Response custom agent | `health-report-investigator` |
    | Agent autonomy level | Review |
    | Status | On |

3. Paste this task prompt:

    ```text
    Run the scheduled Orders service health check for the last 24 hours using
    the proactive-health-check skill. Write every timestamp in UTC with New
    Zealand time in parentheses. Return the findings in this thread and make no
    changes.
    ```

4. Select **Create task** or **Save**, then reopen it and verify the settings.
   Do not leave the portal's default autonomy mode selected.
5. Note the schedule: it runs on weekdays at 09:00 New Zealand Standard Time,
   or 10:00 during daylight time. You will run it now rather than wait for that
   schedule. Each run uses Azure Agent Units; turn the task off when you finish.

    <!-- SCREENSHOT: orders-daily-health-report configured for the assigned agent, with Review mode and its next run -->

### Task 3: Run it and watch the agent

1. Select **Run task now** and open the resulting thread. Watch the agent gather
   records, chart them, and write its findings.
2. Check that the thread includes:
    * The queried environment and time window
    * Current API, PostgreSQL, and VM status
    * Earlier CPU and PostgreSQL incidents, if they fall within the window
    * Missing data and any lack of a historical baseline
    * Sources for the important claims
3. Read the tool-call cards. Calls that read your environment must be queries,
   read-only Azure commands, or charts. Decline any request to change
   resources, send messages, or create issues.

    !!! note "Check what the run actually used"
        The custom agent's tool list does not guarantee which tools a scheduled
        run uses. Check the calls in the thread. If the Application Insights
        query tool is unavailable, use the same records in Log Analytics.

### Task 4: Check the findings yourself

1. Open your Log Analytics workspace's **Logs**. Check recent requests.
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

2. Check recent database calls. This is a second independent view:

    ??? info "Database calls query"
        ```kusto
        AppDependencies
        | where TimeGenerated > ago(15m) and AppRoleName == "orders-api"
        | where DependencyType == "PostgreSQL"
        | extend Samples = tolong(coalesce(ItemCount, 1))
        | summarize Calls = sum(Samples), Failed = sumif(Samples, Success == false)
        ```

3. Compare the current status with these results, the newest CPU samples, and
   the Orders GUI. Compare earlier incident claims with your saved alerts and
   timestamps. The task's full-window totals cover 24 hours; do not compare
   those totals with a 15-minute query.
4. Check the time window. Failures still inside the last 15 minutes can affect
   the reported status even after a reset. A recovered incident outside that
   window should not be described as active.
5. Save three claims as **Supported**, **Partly supported**, or
   **Unsupported**. If a claim lacks evidence, ask the agent for its query and
   time window.

### Task 5: Generate and check the Live Report

1. Keep recent Orders traffic running by refreshing orders and status in the
   Orders GUI.
2. Open **Live Reports** and check whether **Orders Service Health** already
   exists. If it does, open it and select **Open authoring thread** instead of
   creating a copy. Otherwise select **+ New report**.
3. This request tells the agent what the report must show and which status
   rules to use. Paste it into the authoring thread:

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

4. Watch the agent build the report. When it asks you to approve a tool,
   approve only the read-only workspace query tool (`system-mcp-monitor` in
   testing). Decline any request to change Azure resources, send messages, or
   create issues.
5. Wait for the saved version, open the report, and select **Reload**.
6. Check its API, PostgreSQL, database availability, and VM panels:
    * Status agrees with the latest records, not just the whole-day totals.
    * **No data** is not shown as **Healthy**.
    * Recovered incidents are history, not a current outage.
    * The time zone is labeled. New Zealand times should not carry a `+00:00`
      suffix.
7. Compare each status with its table and the queries from
   [Task 4](#task-4-check-the-findings-yourself). A generated report can show
   the wrong status even when its table has data.
8. If a panel disagrees with the logs, record the difference. Describe the
   specific defect in the authoring thread, ask the agent to test the change,
   and check the new version. Do not create another report to fix the existing
   one.
9. If Live Reports is unavailable, record the limitation and keep the log
   queries as your view. Do not report the exercise as completed.

    <!-- SCREENSHOT: Generated Orders Service Health report after Reload, with status and data for the assigned environment -->

!!! note "Report creation uses agent units"
    Creating and updating a report uses agent units. Update the existing
    report rather than creating copies.

### Task 6: Compare the exercises

1. In a new chat, ask the agent to compare your incidents:

    ```text
    Compare today's high-CPU incident and the PostgreSQL connectivity incident on
    the Orders app. For each, give the start time, how it was detected, the
    cause, the customer impact, and the recovery. Use UTC with New Zealand time
    in parentheses. Do not make changes.
    ```

2. Check its times against the alert history and agent threads. Note any mismatch.
3. If your facilitator uploaded the knowledge files, ask which status the SLO
   document would give each area right now and how the results compare with the
   documented baselines. Treat the baselines as fictitious demo values.
4. Use your notes to answer:
    1. Which found each fault sooner: the alert or the next scheduled run?
    2. What could the health check report that a single alert misses?
    3. Which agent or report claim needed your correction?
5. Keep the thread and report available for the final evidence capture. You will
   turn off the recurring task in Module 05.

## Check before moving on

* [ ] You saved and checked the scheduled-task configuration.
* [ ] You watched a run and checked its actual tool calls.
* [ ] You compared three claims with independent evidence.
* [ ] You generated the Live Report, approved only its read-only query tool, and
      checked its status.

## Quick check

??? question "Does a scheduled check replace the alert rules?"
    No. It runs only on its schedule. Alerts can detect a threshold breach
    much earlier; the scheduled check adds a broader review.

??? question "Why check a report that the agent saved successfully?"
    Saving proves it ran, not that its status or calculations are correct.
    Compare the output with the underlying records.

??? question "Which tool do you approve for the report?"
    Only the read-only workspace query tool. Decline anything that changes Azure
    resources, sends messages, or creates issues.

??? question "What can the agent do here that an alert cannot?"
    It reviews a whole period, charts it, compares incidents, and flags missing
    data. An alert only tells you that one threshold was crossed.

## Next

[Reset and preserve evidence :material-arrow-right:](../06-cleanup/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 03 - Respond to PostgreSQL Connectivity Loss](../04-incident-postgresql/index.md)
[Module 05 - Reset and Preserve Evidence :material-arrow-right:](../06-cleanup/index.md)
</div>
