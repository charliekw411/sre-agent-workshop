---
title: Module 05 - Reset and Preserve Evidence
description: Reset the workshop faults, have the agent draft a postmortem, save useful evidence, turn off the recurring task, and hand the environment back.
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - fault reset
  - incident evidence
  - postmortem
  - workshop cleanup
estimated_reading_time: 6
---

<ul class="sre-meta">
<li class="duration">Estimated time: 20 minutes</li>
<li>Module 05</li>
<li>Hands-on</li>
</ul>

## Learning objective

By the end, you can return your environment to a healthy state, use the agent to
draft a postmortem, judge what the agent contributed, and hand the environment
back for organizer cleanup.

## How we'll achieve it

You reset both faults, confirm the app is healthy, and ask the agent to draft a
postmortem from both incidents. You then save your evidence, turn off the
recurring task, and tell your facilitator the final state. Your facilitator
deletes the Azure resources after the workshop; you do not delete them.

## Tasks

### Task 1: Reset both faults

1. Stop any terminal load generator you chose to run with ++ctrl+c++.
2. Open the [CPU exercise](../03-incident-high-cpu/index.md#task-6-reset-cpu)
   and select **Reset incident** in its launcher.
3. Open the [PostgreSQL exercise](../04-incident-postgresql/index.md#task-6-confirm-recovery)
   and select **Reset incident** in its launcher if the agent has not already
   restored access.
4. In the Orders GUI, select **Refresh status** and **Refresh orders**. Confirm
   Liveness, Readiness, and PostgreSQL are all healthy and the CPU graph is back
   in its healthy range. Each button resets only its own scenario, so check both.
   PostgreSQL does not reset automatically when you close the browser.
5. If a reset fails, keep the environment available and tell your facilitator
   which fault remains active. Do not delete resources to hide the failure.

??? info "Terminal alternative"
    With a [prepared terminal](../30-appendix/01-variables.md#terminal-setup-for-an-assigned-environment):

    === "Bash"

        ```bash
        python scripts/workshop.py fault reset
        python scripts/workshop.py fault status
        python scripts/workshop.py smoke
        ```

    === "PowerShell"

        ```powershell
        python .\scripts\workshop.py fault reset
        python .\scripts\workshop.py fault status
        python .\scripts\workshop.py smoke
        ```

    The unqualified `fault reset` resets both scenarios. It does not delete
    resources or stop billing.

### Task 2: Draft the postmortem

1. Find the alert times in each alert rule's **History** and the first-finding
   times in the agent threads for both incidents.
2. In a new agent chat, ask:

    ```text
    Write a short postmortem for today's two Orders incidents: the high-CPU
    incident and the PostgreSQL connectivity incident. For each, give a UTC
    timeline with New Zealand time in parentheses, customer impact, cause,
    detection, recovery, and one prevention suggestion. Mark anything you could
    not verify. Do not make changes.
    ```

3. Check the postmortem against the alert history and your notes. Fix any wrong
   time, cause, or impact, and delete anything unsupported.
4. Calculate for each incident:
    * **Detection delay:** alert fired minus fault started.
    * **Agent time:** agent's first finding minus alert fired.
5. Write two sentences: what the agent did faster or better than you, and what
   you still had to check yourself.

### Task 3: Save the final state

1. In the Orders GUI, select **Refresh orders** and **Refresh status**. Check
   that your saved order is readable and all status cards are healthy.
2. Keep only useful evidence:
    * A healthy Orders screenshot and CPU/failure charts showing recovery
    * The agent findings you checked, including claims you corrected
    * The PostgreSQL alert's matched plan and dedicated-investigator evidence
    * Your corrected postmortem and the follow-up drafts from Module 03
    * Your health-check thread and comparison notes
3. Open **Orders Service Health**, select **Reload**, and use **Download HTML**
   from its menu to save a snapshot. If you could not use the report, record
   why. Treat the downloaded file as potentially sensitive.
4. Redact account, tenant, subscription, and resource identifiers before
   sharing. Do not save tokens, TAPs, or real customer data.
5. Keep your files somewhere durable, not just in a temporary terminal session.

    <!-- SCREENSHOT: Final healthy Orders state and recovered CPU/database charts, with identifiers redacted -->

### Task 4: Turn off the scheduled task

1. In your SRE Agent's **Scheduled tasks**, turn off
   `orders-daily-health-report`.
2. Check that its status is **Off**. If you cannot turn it off, ask your
   facilitator to do it.
3. Leave the skill, custom agent, response plan, report, and run history in
   place for review. The scheduled task uses agent units while it is enabled.

### Task 5: Hand the environment back

1. Tell your facilitator that both faults are reset and the task is off, or
   give them the outstanding error.
2. Select **Disconnect** in the workshop header.

    !!! warning "Do not delete the workshop resource group"
        Resetting faults does not stop Azure charges. Organizers handle resource
        deletion through the [organizer guide](../30-appendix/07-organizer-guide.md#after-the-workshop).
        Use the [self-deployment cleanup](../30-appendix/06-self-deployment.md#delete-your-own-environment)
        only when you deployed and own an independent environment.

## Final check

* [ ] Both faults are inactive and database access is `Allow`.
* [ ] The Orders app is healthy.
* [ ] The recurring task is off.
* [ ] You saved the evidence worth keeping, including your corrected postmortem.
* [ ] Your facilitator knows the environment's final state.

## Quick check

??? question "Does a healthy environment mean billing has stopped?"
    No. The VM, database, monitoring, and agent still exist. Organizers delete
    them after everyone has saved their evidence.

??? question "Why correct the agent's postmortem before sharing it?"
    The agent drafts quickly from the evidence it can see, but it can get a
    time, cause, or impact wrong. You own what gets published.

## Workshop complete

You configured complementary SRE response plans, compared the agent's
investigations with your own, checked its claims, drafted follow-ups and a
postmortem, and compared alerts with a scheduled check and a Live Report. The
agent gathered and explained evidence quickly; you decided what to trust.

[Return to workshop home :material-home:](../../index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 04 - Deploy and Evaluate Proactive SRE Checks](../05-scheduled-health-check/index.md)
[About the authors :material-arrow-right:](../29-about-the-authors/index.md)
</div>
