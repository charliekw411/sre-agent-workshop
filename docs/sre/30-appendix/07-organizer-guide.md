---
title: Organizer Preparation and Cleanup
description: Prepare accounts, isolated environments, browser access, and SRE configuration before class, then delete the cohort after evidence is saved.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - workshop organizers
  - pre-provisioning
  - participant access
  - cleanup
estimated_reading_time: 6
---

This guide is for organizers. Participants start with an account and a healthy,
assigned environment; they do not deploy or delete Azure infrastructure.

## Prepare accounts and infrastructure

Use the existing processes, in this order:

1. [Identity preparation](https://github.com/charliekw411/sre-agent-workshop/blob/main/facilitator-process/identity-provisioning.md):
   create the internal users and workshop group.
2. [Infrastructure provisioning](https://github.com/charliekw411/sre-agent-workshop/blob/main/facilitator-process/infrastructure-provisioning.md):
   deploy and validate one environment per participant.
3. Issue sign-in passes shortly before use and hand each attendee only their
   own credentials.

Infrastructure work is tracked in
[#26](https://github.com/charliekw411/sre-agent-workshop/issues/26); identity,
TAP, and access work is tracked in
[#27](https://github.com/charliekw411/sre-agent-workshop/issues/27).
Test a small cohort before preparing the whole class.

Keep the safe roster separate from private state and credentials.
`.workshop/cohort/roster.json` contains participant/environment display
mappings, not sign-in passes. Do not publish subscription IDs, resource IDs,
or the private state/log directory.

## Prepare the SRE exercises

The core modules require participants to save response-plan settings and
scheduled-task configuration.

`cohort.py` currently grants resource-group Contributor and agent-scoped
SRE Agent Standard User. Standard User alone cannot edit response plans or
create custom agents. The separate identity helper's `assign-access` process
has a different, scoped role model. Do not assume either process has granted
the permissions you intended; inspect and test the actual assignments.

For the core flow, arrange Standard User plus Author on each participant's
own agent, or agent-scoped Administrator if that is your approved access
model. Never expand access to other participant environments. The facilitator
needs Administrator to approve the prepared report's query tool.

For every environment:

1. Check `workshop-sev1-sev2-review` exists, uses Sev1/Sev2 and Review, and has
   no overlapping catch-all plan.
2. Use [proactive setup](08-proactive-extensions.md) to install the skill and
   custom agent from Igor's [#33](https://github.com/charliekw411/sre-agent-workshop/pull/33).
   The existing installer also creates the scheduled task; participants can
   edit that task in Module 04. Turn it off until the exercise.
3. Create **Orders Service Health**, approve only its read-only query tool,
   and check the report against the logs. Participants reload this report;
   they do not need to author or approve it.
4. Keep the agent's Azure runtime roles read-only.

Prepare one shared [site Azure connection](05-site-azure-connection.md) for
the tenant. Do not ask attendees to register individual applications or set
site build variables.

## Check before inviting participants

!!! warning "Known readiness gaps"
    The current browser accepts only resource-group names beginning
    `rg-sre-agent-workshop-`. Cohort deployment creates names such as `rg-01`,
    so those groups will not be discovered. Resolve this through a separate
    runtime change before a browser-based class. Do not rename or retag an
    existing group as a documentation workaround.

    The current cohort grants also do not include the Author access needed
    for the response-plan save. This documentation update does not change
    discovery or RBAC automation.

Use an actual participant account, not just your organizer account, to check:

* TAP sign-in and required MFA onboarding work under the tenant's policy.
* Exactly the assigned environment is accessible, with no other participant's
  resources visible.
* The Orders GUI, database, VM monitoring, and connectors are healthy.
* Response-plan save and scheduled-task create/edit work.
* CPU and PostgreSQL controls, recovery, and investigation links work.
* The prepared Live Report reloads without an attendee approval step.

The cohort's `Ready` result verifies infrastructure stages, not all of these
browser or configuration permissions. PR #33 did not validate a Standard User
participant account or the complete participant portal experience.

If you offer terminal alternatives, supply matching local azd metadata and
only that environment's allowlisted exports. Follow
[terminal setup](01-variables.md#terminal-setup-for-an-assigned-environment).
Exports alone do not let `workshop.py` load an environment. Do not distribute
your entire `.azure` or cohort directory.

## Participant handoff

Give each attendee the shared workshop URL plus their:

* Account, private sign-in pass/expiration, and MFA instructions
* Participant alias, environment name, resource group, and subscription
  display name
* Orders GUI and assigned SRE Agent links
* Prepared Live Report name or link

Keep credentials out of the public roster, repository, screenshots, and
logs. Provide a way to request a replacement pass or report an access problem.

## After the workshop

Confirm participants reset both faults, turned off recurring tasks, and saved
their evidence. Resolve failed resets before collecting the final healthy
state. Preserve only the operational records your organization permits.

Choose one cleanup scope. Review the saved mapping before deletion.

**One environment:**

=== "Bash"

    ```bash
    python scripts/cohort.py cleanup --participant participant-03 --yes
    ```

=== "PowerShell"

    ```powershell
    python .\scripts\cohort.py cleanup --participant participant-03 --yes
    ```

**The entire saved cohort:**

=== "Bash"

    ```bash
    python scripts/cohort.py cleanup --all --yes
    ```

=== "PowerShell"

    ```powershell
    python .\scripts\cohort.py cleanup --all --yes
    ```

Cleanup checks workshop and azd tags against saved state and refuses
mismatched groups. Do not delete by an unverified name pattern or use a
participant's terminal for bulk cleanup.

Verify each intended group no longer exists in its recorded subscription.
Only complete deletion stops ongoing resource charges.

Remove cohort-owned TAPs, users, memberships, and access through the identity
helper's requested cleanup process. See the
[participant script help](https://github.com/charliekw411/sre-agent-workshop/blob/main/scripts/manage-participants.ps1).
Delete any plaintext credential handouts securely.

<div class="sre-nav" markdown>
[Self-deployment :material-arrow-left:](06-self-deployment.md)
[Workshop home :material-home:](../../index.md)
</div>
