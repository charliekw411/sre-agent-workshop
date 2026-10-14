---
title: Module 05 - Reset and Preserve Evidence
description: Stop the workshop faults and recurring task, save useful evidence, and leave the environment for organizer cleanup.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - fault reset
  - incident evidence
  - workshop cleanup
estimated_reading_time: 4
---

<ul class="sre-meta">
<li class="duration">Estimated time: 10 minutes</li>
<li>Module 05</li>
<li>Hands-on</li>
</ul>

## Overview

Leave your environment healthy and save what you learned. Your facilitator
deletes the Azure resources after the workshop; you do not delete them.

## Tasks

### Task 1: Reset both faults

Stop any terminal load generator you chose to run with ++ctrl+c++.

1. Open the [CPU exercise](../03-incident-high-cpu/index.md#task-6-reset-cpu)
   and select **Reset incident** in its launcher.
2. Open the [PostgreSQL exercise](../04-incident-postgresql/index.md#task-6-restore-the-connection)
   and select **Reset incident** in its launcher.
3. Select **Check fault status**. Confirm CPU and PostgreSQL are inactive,
   PostgreSQL access is `Allow`, and application connectivity is `ready`.

Each button resets only its own scenario, so check both. PostgreSQL does not
reset automatically when you close the browser.

If a reset fails, keep the environment available and tell your facilitator
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

### Task 2: Save the final state

In the Orders GUI, select **Refresh orders** and **Refresh status**. Check
that your saved order is readable and all status cards are healthy.

Keep only useful evidence:

* A healthy Orders screenshot and CPU/failure charts showing recovery
* Fault, alert, investigation, reset, and recovery timestamps
* Agent findings you checked, including claims you corrected
* Your health-check thread and comparison notes

Open **Orders Service Health**, select **Reload**, and use **Download HTML**
from its menu to save a snapshot. If you could not use the report, record why.
Treat the downloaded file as potentially sensitive.

Redact account, tenant, subscription, and resource identifiers before sharing.
Do not save tokens, TAPs, or real customer data. Keep your files somewhere
durable, not just in a temporary terminal session.

<!-- SCREENSHOT: Final healthy Orders state and recovered CPU/database charts, with identifiers redacted -->

### Task 3: Turn off the scheduled task

In your SRE Agent's **Scheduled tasks**, turn off
`orders-daily-health-report`. Check that its status is **Off**.
If you cannot turn it off, ask your facilitator to do it.

Leave the skill, custom agent, response plan, report, and run history in place
for review. The scheduled task uses agent units while it is enabled.

### Task 4: Hand the environment back

Tell your facilitator that both faults are reset and the task is off, or
give them the outstanding error. Select **Disconnect** in the workshop header.

!!! warning "Do not delete the workshop resource group"
    Resetting faults does not stop Azure charges. Organizers handle resource
    deletion through the [organizer guide](../30-appendix/07-organizer-guide.md#after-the-workshop).
    Use the [self-deployment cleanup](../30-appendix/06-self-deployment.md#delete-your-own-environment)
    only when you deployed and own an independent environment.

## Final check

* [ ] Both faults are inactive and database access is `Allow`.
* [ ] The Orders app is healthy.
* [ ] The recurring task is off.
* [ ] You saved the evidence worth keeping.
* [ ] Your facilitator knows the environment's final state.

## Quick check

??? question "Does a healthy environment mean billing has stopped?"
    No. The VM, database, monitoring, and agent still exist. Organizers delete
    them after everyone has saved their evidence.

## Workshop complete

You configured SRE response, watched two fault investigations, checked the
agent's claims, and compared alerts with a scheduled check and a Live Report.

[Return to workshop home :material-home:](../../index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 04 - Deploy and Evaluate Proactive SRE Checks](../05-scheduled-health-check/index.md)
[About the authors :material-arrow-right:](../29-about-the-authors/index.md)
</div>
