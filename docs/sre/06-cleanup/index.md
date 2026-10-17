---
title: Module 05 - Reset and Preserve Evidence
description: Have the agent draft a postmortem, turn off the recurring task, and hand the environment back.
ms.date: 2026-10-17
ms.topic: how-to
keywords:
  - incident evidence
  - postmortem
  - workshop cleanup
estimated_reading_time: 4
---

<ul class="sre-meta">
<li class="duration">Estimated time: 10 minutes</li>
<li>Module 05</li>
<li>Hands-on</li>
</ul>

## Learning objective

By the end, you can use the agent to draft a postmortem, judge what the agent
contributed, and hand the environment back for organizer cleanup.

## How we'll achieve it

You ask the agent to draft a postmortem from both incidents, turn off the
recurring task, and tell your facilitator the final state. Your facilitator
deletes the Azure resources after the workshop; you do not delete them.

## Tasks

### Task 1: Draft the postmortem

1. In a new agent chat, ask:

    ```text
    Write a short postmortem for today's two Orders incidents: the high-CPU
    incident and the PostgreSQL connectivity incident. For each, give a UTC
    timeline with New Zealand time in parentheses, customer impact, cause,
    detection, recovery, and one prevention suggestion. Mark anything you could
    not verify. Do not make changes.
    ```

2. Write two sentences: what the agent did faster or better than you, and what
   you still had to check yourself.

### Task 2: Turn off the scheduled task

1. In your SRE Agent, turn off `orders-daily-health-report`. By now you should
   know your way around the SRE portal well enough to find where to do this.
2. Check that its status is **Off**. If you cannot turn it off, ask your
   facilitator to do it.
3. Leave the skill, custom agent, response plan, report, and run history in
   place for review. The scheduled task uses agent units while it is enabled.

### Task 3: Hand the environment back

1. Tell your facilitator that the task is off, or give them the outstanding
   error.
2. Select **Disconnect** in the workshop header.

    !!! warning "Do not delete the workshop resource group"
        Resetting faults does not stop Azure charges. Organizers handle resource
        deletion through the [organizer guide](../30-appendix/07-organizer-guide.md#after-the-workshop).
        Use the [self-deployment cleanup](../30-appendix/06-self-deployment.md#delete-your-own-environment)
        only when you deployed and own an independent environment.

## Final check

* [ ] The recurring task is off.
* [ ] You checked the agent's postmortem.
* [ ] Your facilitator knows the environment's final state.

## Quick check

??? question "Does a healthy environment mean billing has stopped?"
    No. The VM, database, monitoring, and agent still exist. Organizers delete
    them after everyone has finished.

??? question "Why correct the agent's postmortem before sharing it?"
    The agent drafts quickly from the evidence it can see, but it can get a
    time, cause, or impact wrong. You own what gets published.

## Workshop complete

Congrats, you've now completed the workshop! You configured complementary SRE
response plans, compared the agent's investigations with your own, checked its
claims, drafted follow-ups and a postmortem, and compared alerts with a
scheduled check and a Live Report. The agent gathered and explained evidence
quickly; you decided what to trust.

It took time and effort to set this workshop up, so we'd really appreciate some
feedback on this! Thank you :smile:

[Give feedback :material-message-text:](https://forms.cloud.microsoft/r/JUWtiTc4A0){ .md-button .md-button--primary }
[Return to workshop home :material-home:](../../index.md){ .md-button }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 04 - Deploy and Evaluate Proactive SRE Checks](../05-scheduled-health-check/index.md)
[About the authors :material-arrow-right:](../29-about-the-authors/index.md)
</div>
