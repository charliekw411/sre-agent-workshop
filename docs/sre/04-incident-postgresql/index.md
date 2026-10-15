---
title: Module 03 - Respond to PostgreSQL Connectivity Loss
description: Block the app's private database connection, watch the dedicated investigator work, draft the follow-up, and restore access without changing orders.
ms.date: 2026-10-16
ms.topic: how-to
keywords:
  - postgresql
  - network fault
  - azure sre agent
  - incident response
estimated_reading_time: 10
---

<ul class="sre-meta">
<li class="duration">Estimated time: 45 minutes</li>
<li>Module 03</li>
<li class="incident">Incident 2 of 2</li>
</ul>

## Learning objective

By the end, you can follow a PostgreSQL connectivity failure from the customer
app to the alert and the dedicated incident investigator, check the agent's
explanation against the network rule, use the agent to draft an incident
follow-up, and restore access without changing orders.

## How we'll achieve it

You block the connection between your Orders VM and PostgreSQL, watch how the
app, alert, and investigator respond, and check the agent's explanation. Then
you restore the connection, confirm your order is unchanged, and ask the agent
to draft the incident follow-up.

### What changes

The fault changes one network security group (NSG) rule. An NSG is Azure's
firewall rule set for a subnet:

| Property | Fixed value |
| --- | --- |
| Name | `PostgreSqlFaultInjection` |
| Direction and protocol | Outbound TCP |
| Source | Orders subnet, `10.240.0.0/27` |
| Destination | PostgreSQL subnet, `10.240.0.32/27`, port `5432` |
| Priority | `100` |
| Access | `Allow` normally; `Deny` during the fault |

An NSG change affects new connections, not established ones. The launcher
therefore restarts only `orders-api` to close its existing database connections.
It does not restart the VM or PostgreSQL, change orders, or block other traffic.

## Tasks

### Task 1: Review the SRE and network settings

Two response plans share the work. PostgreSQL alerts go to a dedicated
investigator; everything else stays with the main agent.

1. In your SRE Agent, check both plans:

    | Plan | Title contains | Handling agent |
    | --- | --- | --- |
    | `workshop-sev1-sev2-review` | `alert-orders-h` | Coordinating or main agent |
    | `orders-incident-workflow` | `postgresql` | `orders-alert-investigator` |

2. Check that both plans are On, handle Sev1 and Sev2, and use **Review** mode.
   Do not remove either title filter.
3. In **Monitor > Alerts > Alert rules**, filter to your resource group. Check
   that `alert-orders-postgresql-connectivity` is enabled, is Sev1, and fires
   when a PostgreSQL dependency fails within five minutes.
4. Open your NSG's **Outbound security rules**. Find `PostgreSqlFaultInjection`
   and check it against the table above. Do not change it.

### Task 2: Save a healthy order

1. In the launcher below, select **Check fault status**. CPU and PostgreSQL
   should be inactive, access should be `Allow`, and application connectivity
   should be `ready`.
2. In your Orders GUI, create a synthetic order with customer ID
   `postgresql-incident`. Save its order ID, product, and quantity. You will
   read the same order after recovery.
3. Select **Refresh status** and **Refresh orders**. All three status cards and
   the order list should work before injection.

### Task 3: Block the database connection

1. Write the current time (UTC) in your evidence log as **Fault started**, then
   select **Block PostgreSQL access**.

    <div data-sre-incident="postgresql">
    <p><strong>JavaScript is required for the launcher.</strong> If it is unavailable, use the terminal alternative below after your facilitator has prepared it.</p>
    </div>

2. Confirm success: the rule is `Deny`, the API has restarted, and the launcher
   has checked the expected HTTP responses. A rule change alone is not enough.
3. Select **Refresh status** and **Refresh orders** in the Orders GUI and check:

    | Check | During the fault |
    | --- | --- |
    | Liveness, `/health/live` | HTTP 200: the process is running |
    | Readiness, `/health/ready` | HTTP 503: the database is unreachable |
    | PostgreSQL, `/database` | Unavailable, HTTP 503 |
    | Orders, `/orders` | Unavailable, HTTP 503 |

    !!! warning "Check before retrying"
        If injection is interrupted, select **Check fault status** before retrying.
        Do not broaden the rule, stop PostgreSQL, or enable database public access.
        This fault stays in place until you reset it.

??? info "Terminal alternative"
    First complete [terminal setup](../30-appendix/01-variables.md#terminal-setup-for-an-assigned-environment).

    === "Bash"

        ```bash
        python scripts/workshop.py fault postgresql
        python scripts/workshop.py fault status
        ```

    === "PowerShell"

        ```powershell
        python .\scripts\workshop.py fault postgresql
        python .\scripts\workshop.py fault status
        ```

### Task 4: Watch the alert and the agent

1. Keep the Orders tab visible and refresh orders a few times. Watch failed
   PostgreSQL calls appear in the launcher's graph. Missing samples are not
   successful calls.
2. Open **Monitor > Alerts**, filtered to your resource group. Find the fired
   `alert-orders-postgresql-connectivity` alert, then use **Open SRE Agent
   incidents** to follow its investigation.
3. Confirm the PostgreSQL alert matched `orders-incident-workflow` and the
   investigation used `orders-alert-investigator`. The HTTP 5xx alert may also
   fire because orders return 503; it should remain on
   `workshop-sev1-sev2-review`.
4. Match the alert and timestamps even if the agent uses an existing thread.
   Allow for monitoring and alert delays; do not toggle the fault to make a
   chart update.
5. Write the alert time as **Alert fired** and the time of the agent's first
   finding as **Agent's first finding** in your evidence log.

    <!-- SCREENSHOT: Failed PostgreSQL calls, fired alert, and agent investigation for the same assigned environment -->

### Task 5: Check the agent's explanation

1. Read the initial investigation.
2. If needed, ask:

    ```text
    Use orders-azure-monitor-rca for this PostgreSQL connectivity alert. Confirm
    the response plan and handling agent, whether the VM and database stayed
    running, which network rule changed, and how requests were affected. Cite the
    evidence for the cause and the smallest recovery step. Do not make changes.
    ```

3. Ask how it worked:

    ```text
    Which workshop runbook or procedure did you follow for this alert, and which
    steps did you complete? Say if you did not use one.
    ```

    The investigator has the workshop dependency-failure runbook as reference.
4. Check its answer in the portal:
    * PostgreSQL still shows **Ready**, with public access disabled.
    * `PostgreSqlFaultInjection` shows `Deny`; its other settings are unchanged.
    * **Activity log** shows the rule write near the fault start.
    * The Orders GUI shows a running process but failed database-backed requests.
5. Compare the explanation with the evidence. The deliberate API restart closes
   old connections; the denied network rule causes the continuing outage. Write
   one agent claim you checked, and the result, in your evidence log. Mark any
   claim the evidence does not support.

??? info "Optional stretch: see the approval gate"
    The plans run in **Review** mode, so the agent must ask you before it changes
    anything. While the fault is still active, ask:

    ```text
    Propose the narrowest reversible mitigation for this incident, with its
    rollback and how to validate recovery. Do not run it.
    ```

    Read the proposal. If the agent asks for approval to run an action, decline
    it. You reset the fault with the launcher in Task 6. Note how the proposal
    names a specific change and a rollback, rather than a broad fix.

### Task 6: Restore the connection

1. Write the current time as **Reset** in your evidence log. Then select
   **Reset incident** and **Check fault status**. Expect PostgreSQL inactive,
   access `Allow`, and application connectivity `ready`. CPU should remain
   inactive.
2. In the Orders GUI, refresh orders and status. Open the order you saved in
   Task 2 and check its product and quantity. All status cards should recover.
3. Check that the latest database calls succeed and wait for the alert to
   resolve. Old failures remain visible in the chart; they are incident
   history.
4. Write the time the app looked healthy again as **Recovered**.
5. If reset fails, check status and use
   [fault troubleshooting](../30-appendix/02-troubleshooting.md#fault-controls)
   or ask your facilitator. Leave no database fault active.

??? info "Terminal reset"
    With the [prepared terminal](../30-appendix/01-variables.md#terminal-setup-for-an-assigned-environment):

    ```bash
    python scripts/workshop.py fault reset-postgresql
    python scripts/workshop.py fault status
    python scripts/workshop.py smoke
    ```

    In PowerShell, use `python .\scripts\workshop.py` for the same commands.

### Task 7: Draft the follow-up

After an incident, someone writes the issue and the summary email. The
investigator has skills for both. In this workshop they only produce drafts.

1. In the incident thread, ask:

    ```text
    Use orders-github-issue-followup to draft a GitHub issue for this incident,
    and orders-email-incident-followup to draft a summary email. Draft only: do
    not publish or send anything. Include a UTC timeline, customer impact, the
    cause, the evidence, and the recovery step.
    ```

2. Check each draft against your evidence log:
    * The fault, alert, reset, and recovery times match yours.
    * The cause and impact match what you verified in Task 5.
    * It contains no credentials, tokens, or real customer data.
3. Confirm nothing was sent or created. The classroom setup binds no GitHub
   repository or Outlook recipient, so the agent should say what is missing.
   Publishing would need a separate request and your approval.
4. Edit one sentence you would change before sharing the draft. Keep the drafts
   with your evidence.

## Check before moving on

* [ ] Only the documented rule's access changed.
* [ ] The PostgreSQL alert reached `orders-incident-workflow` and
      `orders-alert-investigator`.
* [ ] Any HTTP 5xx alert remained on the original response plan.
* [ ] You checked the agent's explanation against the rule and app.
* [ ] Access is `Allow`, the app is healthy, and your order is unchanged.
* [ ] You reviewed the follow-up drafts, and nothing was sent.
* [ ] Your PostgreSQL column in the
      [evidence log](../01-deploy-and-validate/index.md#your-evidence-log) is
      filled in.

## Quick check

??? question "Why can liveness stay green during this outage?"
    The API process is running, but it cannot reach PostgreSQL. Liveness alone
    does not prove that customers can place orders.

??? question "Why not stop the database instead?"
    That would be a different incident. This exercise isolates one connection
    and leaves the database and its data intact.

??? question "Why can both response plans stay enabled?"
    Their scalar title filters are complementary. PostgreSQL titles match the
    dedicated plan; CPU and HTTP 5xx titles match the original plan.

??? question "What did the dedicated investigator and its drafts save you?"
    It gathered evidence across the app, network, and Activity log, then turned
    it into a timeline and a ready-to-edit issue and email. You still verified
    the facts and decided what to publish.

## Next

[Deploy and evaluate proactive SRE checks :material-arrow-right:](../05-scheduled-health-check/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 02 - Configure SRE and Respond to High CPU](../03-incident-high-cpu/index.md)
[Module 04 - Deploy and Evaluate Proactive SRE Checks :material-arrow-right:](../05-scheduled-health-check/index.md)
</div>
