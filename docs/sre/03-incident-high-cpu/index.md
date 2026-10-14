---
title: Module 02 - Configure SRE and Respond to High CPU
description: Save the workshop response-plan settings, start a CPU fault, watch the agent investigate, and check recovery.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - cpu
  - response plan
  - azure sre agent
  - incident response
estimated_reading_time: 8
---

<ul class="sre-meta">
<li class="duration">Estimated time: 30 minutes</li>
<li>Module 02</li>
<li class="incident">Incident 1 of 2</li>
</ul>

## Overview

Configure how the agent receives alerts, then make your VM busy for ten
minutes. Watch the alert start an investigation, check the agent's explanation,
and reset the fault.

You are changing SRE configuration, not deploying infrastructure.

## What changes

The fault starts two CPU workers on your assigned VM through Azure VM Run
Command. It has a ten-minute limit, a 256 MiB memory cap, and reduced scheduling
priority. It does not change PostgreSQL.

## Tasks

### Task 1: Apply the response-plan settings

Open your assigned SRE Agent. Go to **Response plans** or
**Builder > Incident response plans** and open `workshop-sev1-sev2-review`.
The deployment already created it.

Apply these settings and select **Save**:

| Setting | Value |
| --- | --- |
| Incident platform | Azure Monitor |
| Severity | Sev1 and Sev2 |
| Handling agent | Coordinating or main agent |
| Autonomy level | Review |
| Enabled | On |

Open the plan again and check the saved values. Keep its existing
merge/reinvestigation settings. Do not add another overlapping plan.

If the plan is missing or you cannot save it, ask your facilitator. They must
prepare configuration access on your agent; you do not need subscription
ownership.

In **Azure Monitor > Alerts > Alert rules**, filter to your resource group.
Check that `alert-orders-high-cpu` is enabled, is Sev2, and watches average CPU
above 80 percent for five minutes.

<!-- SCREENSHOT: Assigned agent's saved Azure Monitor response plan with Sev1/Sev2 and Review mode -->

### Task 2: Check the starting state

Confirm the site header shows your assigned environment. In the launcher below,
select **Check fault status**. Both faults should be inactive, PostgreSQL access
should be `Allow`, and application connectivity should be `ready`.

Open your Orders GUI and select **Refresh status** and **Refresh orders**.
Note the normal response time. Keep that tab visible for its regular status
checks.

Look at the CPU graph below and note the healthy CPU range. Record the fault
start time in your notes; use UTC when comparing it with portal events.

### Task 3: Start the CPU fault

Select **Run CPU incident**.

<div data-sre-incident="cpu">
<p><strong>JavaScript is required for the launcher.</strong> If it is unavailable, use the terminal alternative below after your facilitator has prepared it.</p>
</div>

The graph shows average CPU in one-minute intervals. It refreshes automatically.
You should see CPU rise above the alert threshold.

!!! warning "Check before retrying"
    A timed-out request may still have started the fault. Select **Check fault
    status** before trying again. Do not start the PostgreSQL fault at the same
    time.

??? info "Terminal alternative"
    First complete [terminal setup](../30-appendix/01-variables.md#terminal-setup-for-an-assigned-environment).
    Do not run these commands against an unprepared or different environment.

    === "Bash"

        ```bash
        python scripts/workshop.py fault cpu 600 2
        python scripts/workshop.py fault status
        ```

    === "PowerShell"

        ```powershell
        python .\scripts\workshop.py fault cpu 600 2
        python .\scripts\workshop.py fault status
        ```

### Task 4: Watch the alert and the agent

In the Orders GUI, select **Refresh orders** during the fault. Record any
slower response, timeout, or failure. Requests may still succeed; high CPU
does not guarantee a complete outage.

Use **Open Azure Monitor alert**, or open **Monitor > Alerts** and filter to
your resource group. Wait for `alert-orders-high-cpu` to show **Fired**.
It uses a five-minute average, so it will not fire immediately.

Select **Open SRE Agent incidents**. Find the investigation for that alert
and watch its tool calls and findings appear. It may update an existing thread.

Record the alert time and investigation link. If the alert does not fire or
no investigation appears, use [troubleshooting](../30-appendix/02-troubleshooting.md#telemetry-and-alerts);
do not widen the fault.

### Task 5: Check the agent's explanation

Read the initial findings before adding a prompt. If you need more detail, ask:

```text
Explain the high-CPU alert on the Orders VM. Show when CPU rose, how requests
were affected, and any VM Run Command event near the start. Cite the evidence
for your cause and proposed recovery. Do not make changes.
```

Check three things yourself:

* The CPU chart agrees with the agent's values and time range.
* The Orders GUI or Application Insights **Performance** supports its claim
  about customer impact.
* Your resource group's **Activity log** shows the VM Run Command near the
  fault start.

Record which claims you checked and correct anything unsupported. Do not
accept a cause only because it sounds plausible.

<!-- SCREENSHOT: CPU spike, fired alert, and agent findings for the same assigned VM and time range -->

### Task 6: Reset CPU

Select **Reset incident** in the CPU launcher, then **Check fault status**.
CPU should be inactive; the PostgreSQL state should be unchanged.

Refresh orders and status. Watch the newest CPU samples return toward the
healthy range. The alert resolves after its evaluation window is healthy.
If the ten-minute fault already expired, still check recovery.

Save the reset and recovery times. Do not start the next incident until the
app is healthy.

??? info "Terminal reset"
    With the [prepared terminal](../30-appendix/01-variables.md#terminal-setup-for-an-assigned-environment):

    ```bash
    python scripts/workshop.py fault reset-cpu
    python scripts/workshop.py fault status
    python scripts/workshop.py smoke
    ```

    In PowerShell, use `python .\scripts\workshop.py` for the same commands.

## Check before moving on

* [ ] You saved and checked the Review response plan.
* [ ] CPU crossed the threshold and the alert reached the agent.
* [ ] You checked the agent's cause and impact claims.
* [ ] CPU is inactive and the Orders app is healthy.

## Quick check

??? question "Why does the alert arrive after CPU first rises?"
    The rule needs a five-minute average above its threshold. Detection is
    later than the start of the problem.

??? question "Did stopping the fault fix a production CPU problem?"
    No. It ended this exercise. A real incident still needs the offending
    workload, demand, or capacity problem understood.

## Next

[Respond to PostgreSQL connectivity loss :material-arrow-right:](../04-incident-postgresql/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 01 - Access and Understand the Workshop](../01-deploy-and-validate/index.md)
[Module 03 - Respond to PostgreSQL Connectivity Loss :material-arrow-right:](../04-incident-postgresql/index.md)
</div>
