---
title: Incident Workflow Setup and Routing
description: Install and verify the dedicated PostgreSQL incident workflow used by the participant class.
ms.date: 2026-10-15
ms.topic: how-to
keywords:
  - azure sre agent
  - incident workflow
  - response plans
  - custom agent
---

This organizer setup prepares the dedicated investigator used in
[Module 03](../04-incident-postgresql/index.md). Participants inspect and use
the configuration; the facilitator installs it before class.

## What the installer adds

`incident-workflow install` adds:

| Component | Name |
| --- | --- |
| Response plan | `orders-incident-workflow` |
| Custom agent | `orders-alert-investigator` |
| RCA skill | `orders-azure-monitor-rca` |
| GitHub follow-up skill | `orders-github-issue-followup` |
| Email follow-up skill | `orders-email-incident-followup` |

The two response plans remain enabled with complementary scalar title filters.
The dedicated PostgreSQL plan runs in **Autonomous** mode; the original plan
stays in **Review** mode:

| Plan | Title contains | Handles | Mode |
| --- | --- | --- | --- |
| `workshop-sev1-sev2-review` | `alert-orders-h` | CPU and HTTP 5xx alerts | Review |
| `orders-incident-workflow` | `postgresql` | PostgreSQL connectivity alerts | Autonomous |

This partition prevents one alert from matching both plans. Do not remove
either title filter or add another unrestricted Sev1/Sev2 plan.

### What the autonomous investigator may change

The investigator's UAMI already has Contributor on the workshop resource group,
so it acts without approval. Its tools and instructions limit it to one Azure
write: `az network nsg rule update` setting `PostgreSqlFaultInjection` to
`Allow`. It cannot run a terminal, VM Run Command, or restart anything. It
verifies by reading the rule back and checking fresh Log Analytics telemetry.
GitHub and email follow-ups stay draft-only and approval-gated. The agent-level
default remains Review, so every other plan and task is unchanged.

## Install one environment

Select the intended azd environment, then run:

```powershell
$env:AZURE_ENV_NAME = "participant-01"
python .\scripts\workshop.py incident-workflow install
python .\scripts\workshop.py incident-workflow status
```

For the full participant cohort, use
[facilitator runbook step 4](https://github.com/charliekw411/sre-agent-workshop/blob/main/facilitator-process/README.md#4-prepare-every-sre-agent).

A healthy result reports:

```json
{
  "installed": true,
  "matchesDefinition": true,
  "phase": "active",
  "responsePlan": "orders-incident-workflow",
  "handlingAgent": "orders-alert-investigator",
  "problems": []
}
```

Check the two plans in the portal and confirm that both are On and use Sev1 and
Sev2. `orders-incident-workflow` must use **Autonomous** mode and
`workshop-sev1-sev2-review` must use **Review** mode. Confirm the three skills
and the custom agent are present.

## External follow-ups

The classroom installation does not bind a source repository, GitHub issue
repository, Outlook connector, or email recipients. The GitHub and email skills
remain draft-only.

The installer supports explicit trusted destinations through
`--source-repository`, `--github-repository`, `--outlook-connector`, and
repeatable `--email-to` options. Do not use them without approved test
destinations, authenticated connections, and Ask policies for each write.

## Recovery

The ignored file
`.workshop/<environment>/incident-workflow-state.json` stores the original
response-plan configuration required for safe removal. Do not delete it while
the workflow is installed.

To restore the original single-plan routing:

```powershell
python .\scripts\workshop.py incident-workflow remove
python .\scripts\workshop.py incident-workflow status
```

Removal disables the dedicated plan, restores the original response-plan
filter, and deletes only the dedicated plan, custom agent, and three skills.
If status reports drift or a partial operation, preserve the state file and
resolve the named conflict instead of editing both plans manually.
