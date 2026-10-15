---
title: Health-check Setup
description: Install the supplied health-check components and prepare participants to generate the Live Report in Module 04.
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - scheduled tasks
  - live reports
  - azure sre agent
estimated_reading_time: 6
---

The core [proactive exercise](../05-scheduled-health-check/index.md) uses
prepared components. Use this guide as an organizer or independent environment
owner. Participants generate the Live Report themselves in the module.

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
run's tool calls, keep Review mode, and retain the read-only custom-agent tool
list. The UAMI's Contributor role is limited to the participant resource group;
Monitoring Contributor exists for the Azure Monitor alert lifecycle.

The optional Application Insights query tool was unavailable in those runs.
The skill uses the same data in the Log Analytics `App*` tables and reports
that limitation. It must not invent a cross-check or a baseline.

## Live Report

Participants generate **Orders Service Health** themselves in
[Module 04, Task 5](../05-scheduled-health-check/index.md#task-5-generate-and-check-the-live-report),
which contains the authoring prompt. Do not pre-create the report for the class.

Creating and reloading reports requires an appropriate SRE Agent user role.
Approving the report's tools and deleting reports require Administrator. The
cohort grants each participant Administrator on only their own agent, so they
can approve the read-only query tool themselves. In testing, one approval was
needed for the read-only `system-mcp-monitor` workspace query tool. Tell
participants to decline writes, messages, or issue creation.

To rehearse or test the report, use the Module 04 prompt in a test environment
and check each status against the
[Module 04 queries](../05-scheduled-health-check/index.md#task-4-check-the-findings-yourself).
Live Reports is a preview feature. If it is unavailable, record the limitation
and keep the log queries as the view.

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
