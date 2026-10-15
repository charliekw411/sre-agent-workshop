---
title: Module 02 - Configure SRE and Respond to High CPU
description: Save the workshop response-plan settings, start a CPU fault, race the agent's investigation, and check recovery.
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - cpu
  - response plan
  - azure sre agent
  - incident response
estimated_reading_time: 10
---

<ul class="sre-meta">
<li class="duration">Estimated time: 40 minutes</li>
<li>Module 02</li>
<li class="incident">Incident 1 of 2</li>
</ul>

## Learning objective

By the end, you can configure how the SRE Agent receives alerts, compare its
speed and accuracy with your own investigation of a high-CPU alert, and confirm
recovery.

## How we'll achieve it

You set up the agent's response plan, then make your VM busy for ten minutes.
While the alert is pending, you start your own investigation. When the agent
finishes, you compare its answer and timing with yours, then reset the fault.
You are changing SRE settings, not deploying infrastructure.

### What changes

The fault starts two CPU workers on your VM through Azure VM Run Command. It
stops after ten minutes, uses at most 256 MiB of memory, and runs at low
priority. It does not change PostgreSQL.

## Tasks

### Task 1: Apply the response-plan settings

A response plan tells the agent which alerts to investigate and how much it may
do on its own.

1. Open your SRE Agent. Go to **Response plans** or
   **Builder > Incident response plans** and open `workshop-sev1-sev2-review`.
   The deployment already created it.
2. Apply these settings and select **Save**:

    | Setting | Value |
    | --- | --- |
    | Incident platform | Azure Monitor |
    | Severity | Sev1 and Sev2 |
    | Handling agent | Coordinating or main agent |
    | Autonomy level | Review |
    | Enabled | On |
    | Title contains | `alert-orders-h` |

3. Open the plan again and check the saved values. Leave the merge and
   reinvestigation settings and the title filter as they are. Do not add another
   plan: `orders-incident-workflow` already handles PostgreSQL alerts.
4. If the plan is missing or you cannot save it, ask your facilitator.
5. In **Azure Monitor > Alerts > Alert rules**, filter to your resource group.
   Check that `alert-orders-high-cpu` is enabled, is Sev2, and fires when average
   CPU is above 80 percent for five minutes.

    <!-- SCREENSHOT: Assigned agent's saved Azure Monitor response plan with Sev1/Sev2 and Review mode -->

### Task 2: Check the starting state

1. In the launcher below, select **Check fault status**. Both faults should be
   inactive, PostgreSQL access should be `Allow`, and application connectivity
   should be `ready`.
2. In your Orders GUI, select **Refresh status** and **Refresh orders**. Keep
   that tab visible. Note the normal response time.
3. Look at the CPU graph below and note the healthy CPU range.

### Task 3: Start the CPU fault

1. Write the current time (UTC) in your evidence log as **Fault started**, then
   select **Run CPU incident**.

    <div data-sre-incident="cpu">
    <p><strong>JavaScript is required for the launcher.</strong> If it is unavailable, use the terminal alternative below after your facilitator has prepared it.</p>
    </div>

2. Watch the graph, which shows average CPU in one-minute intervals and
   refreshes automatically. You should see CPU rise above the alert threshold.

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

### Task 4: Race the agent

The alert uses a five-minute average, so it will not fire immediately. Use the
wait to investigate on your own.

1. In the Orders GUI, select **Refresh orders** a few times. Note any slower
   response, timeout, or failure. Requests may still succeed; high CPU does not
   guarantee an outage.
2. Set a five-minute timer. Without the agent, find what is wrong. Open your VM
   in the portal and check **Metrics** (Percentage CPU) and **Activity log**.
3. Write your best explanation in one or two sentences: what is wrong, since
   when, and what started it.
4. Open **Monitor > Alerts**, filtered to your resource group, or select
   **Open Azure Monitor alert**. Wait for `alert-orders-high-cpu` to show
   **Fired**. Write the time as **Alert fired** in your evidence log.
5. Select **Open SRE Agent incidents**. Find the investigation for that alert
   and watch its tool calls and findings appear. It may update an existing
   thread. Write the time of its first finding as **Agent's first finding**.
6. If the alert does not fire or no investigation appears, use
   [troubleshooting](../30-appendix/02-troubleshooting.md#telemetry-and-alerts);
   do not widen the fault.

### Task 5: Check the agent's explanation

1. Read the agent's findings and compare them with the explanation you wrote in
   Task 4. Note what the agent found that you missed, and what you found that it
   did not.
2. If you need more detail, ask:

    ```text
    Explain the high-CPU alert on the Orders VM. Show when CPU rose, how requests
    were affected, and any VM Run Command event near the start. Cite the evidence
    for your cause and proposed recovery. Do not make changes.
    ```

3. Ask how it worked:

    ```text
    Which workshop runbook or procedure did you follow for this alert, and which
    steps did you complete? Say if you did not use one.
    ```

    The agent has the workshop runbooks as reference. Its answer shows whether it
    followed a known procedure or improvised.
4. Check three things yourself:
    * The CPU chart agrees with the agent's values and time range.
    * The Orders GUI or Application Insights **Performance** supports its claim
      about customer impact.
    * Your resource group's **Activity log** shows the VM Run Command near the
      fault start.
5. Write one agent claim you checked, and the result, in your evidence log. If a
   claim has no evidence, treat it as unsupported. Do not accept a cause only
   because it sounds plausible.

    <!-- SCREENSHOT: CPU spike, fired alert, and agent findings for the same assigned VM and time range -->

### Task 6: Reset CPU

1. Write the current time as **Reset** in your evidence log. Then select
   **Reset incident** in the CPU launcher and **Check fault status**. CPU should
   be inactive; the PostgreSQL state should be unchanged.
2. Refresh orders and status. Watch the newest CPU samples return toward the
   healthy range. The alert resolves after its evaluation window is healthy.
   If the ten-minute fault already expired, still check recovery.
3. Write the time the app looked healthy again as **Recovered**. Do not start the
   next incident until the app is healthy.

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
* [ ] You compared the agent's explanation with your own and checked one claim.
* [ ] Your CPU column in the [evidence log](../01-deploy-and-validate/index.md#your-evidence-log)
      is filled in.
* [ ] CPU is inactive and the Orders app is healthy.

## Quick check

??? question "Why does the alert arrive after CPU first rises?"
    The rule needs a five-minute average above its threshold. Detection is
    later than the start of the problem.

??? question "Did stopping the fault fix a production CPU problem?"
    No. It ended this exercise. A real incident still needs the offending
    workload, demand, or capacity problem understood.

??? question "What did the agent add compared with your own investigation?"
    Compare your five-minute result with the agent's. Typically it correlates
    metrics, requests, and the Activity log together and cites its evidence. You
    still check its claims.

## Next

[Respond to PostgreSQL connectivity loss :material-arrow-right:](../04-incident-postgresql/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 01 - Access and Understand the Workshop](../01-deploy-and-validate/index.md)
[Module 03 - Respond to PostgreSQL Connectivity Loss :material-arrow-right:](../04-incident-postgresql/index.md)
</div>
