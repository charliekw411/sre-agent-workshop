---
title: Module 03 - Respond to PostgreSQL Connectivity Loss
description: Block the app's private database connection, watch the dedicated investigator diagnose and fix it autonomously, and draft the follow-up.
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
app to the alert and the dedicated incident investigator, trace how the agent
reasoned and autonomously restored access, check its fix against the network
rule and the app, and use the agent to draft an incident follow-up.

## How we'll achieve it

You block the connection between your Orders VM and PostgreSQL, then watch the
alert wake the investigator. It runs in **Autonomous** mode, so it diagnoses
the cause and fixes it without asking you. You follow its incident thread in the
SRE Agent portal, check each claim and the change it made, confirm your order is
unchanged, and ask the agent to draft the incident follow-up.

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

The investigator's fix is narrower still: it sets this one rule back to `Allow`.
It makes no other change and does not restart anything.

## Tasks

### Task 1: Review the SRE and network settings

Two response plans share the work. PostgreSQL alerts go to a dedicated
investigator; everything else stays with the main agent.

1. In your SRE Agent, check both plans:

    | Plan | Title contains | Handling agent | Autonomy level |
    | --- | --- | --- | --- |
    | `workshop-sev1-sev2-review` | `alert-orders-h` | Coordinating or main agent | Review |
    | `orders-incident-workflow` | `postgresql` | `orders-alert-investigator` | **Autonomous** |

2. Check that both plans are On and handle Sev1 and Sev2. The PostgreSQL plan
   must use **Autonomous** mode; the original plan stays on **Review**. Do not
   remove either title filter.
3. Open your resource group, search for `alert-orders-postgresql-connectivity`,
   and open it. Alternatively, search for **Monitor** in the top bar and open
   **Alerts > Alert rules**, filtered to your resource group. Check that the rule
   is enabled, is Sev1, and fires when a PostgreSQL dependency fails within five
   minutes.
4. Check the network rule the fault will change. Do not edit it.
    1. In the Azure portal, open your resource group and select the network
       security group whose name starts with `nsg-orders-`.
    2. In the left menu, under **Settings**, select **Outbound security rules**.
    3. Select the rule named `PostgreSqlFaultInjection`.
    4. Check that **Priority** is `100`, **Protocol** is `TCP`, **Source** is
       the Orders subnet `10.240.0.0/27`, **Destination** is the PostgreSQL
       subnet `10.240.0.32/27`, **Destination port ranges** is `5432`, and
       **Action** is `Allow`.
    5. Close the rule pane without saving. After you block access in Task 3, you
       will return here and see **Action** change to `Deny`.

    <!-- SCREENSHOT: Assigned NSG Outbound security rules list with PostgreSqlFaultInjection selected and Action Allow -->

### Task 2: Save a healthy order

1. In your Orders GUI, select **Refresh status** and **Refresh orders**. All
   three status cards should show healthy results: Liveness HTTP 200, Readiness
   HTTP 200, and PostgreSQL Ready.
2. Create a synthetic order with customer ID `postgresql-incident`. Save its
   order ID, product, and quantity. You will read the same order after recovery.
3. Select **Refresh orders** again. The order list should include your order
   before injection.

### Task 3: Block the database connection

1. Select **Block PostgreSQL access**.

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
        If injection is interrupted, check the Orders GUI status cards and the
        launcher graph before retrying.
        Do not broaden the rule, stop PostgreSQL, or enable database public access.
        The fault stays in place until the investigator restores the rule.

    !!! note "Run the incident once per session"
        The agent usually restores the rule within two minutes of the alert. The
        alert then takes about 15 minutes to resolve, and Azure Monitor raises no
        new alert while it is still firing. An alert that repeats within three
        hours joins the earlier incident thread, which keeps that thread's
        original mode. Inject the fault once and follow that thread.

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
   investigation used `orders-alert-investigator` in **Autonomous** mode. The
   HTTP 5xx alert may also fire because orders return 503; it stays on
   `workshop-sev1-sev2-review` in Review mode. If that thread proposes its own
   Azure change, decline it; the investigator already owns the fix.
4. Match the alert and timestamps even if the agent uses an existing thread.
   Allow for monitoring and alert delays; do not toggle the fault or restore the
   rule yourself while the agent is working. Expect the alert in about a minute
   and the agent's fix within a couple of minutes after that.
5. Note the time the alert fired and the time of the agent's first finding.

    <!-- SCREENSHOT: Failed PostgreSQL calls, fired alert, and agent investigation for the same assigned environment -->

### Task 5: Follow the agent's reasoning and fix

The agent does not wait for approval. Read its thread from the top and note
each step, so you can explain how it reached its decision.

1. In the incident thread, find these steps in order:
    * **Observe:** failed PostgreSQL dependencies and readiness, with normal VM
      CPU.
    * **Hypothesize:** a network boundary change, not a stopped database.
    * **Confirm:** the `PostgreSqlFaultInjection` rule is `Deny` and the
      Activity log shows the rule write.
    * **Act:** one Azure change setting that rule to `Allow`, with the previous
      value recorded as the rollback.
    * **Verify:** the rule read back as `Allow`, then fresh successful
      PostgreSQL dependency and availability samples. Telemetry arrives a few
      minutes late, so the agent may report **Mitigated, recovery pending
      telemetry** first. That is expected: it will not claim recovery from the
      rule change alone.
2. If needed, ask:

    ```text
    Summarize what you did for this PostgreSQL connectivity alert: the response
    plan and handling agent, the evidence for the cause, the exact change you
    made and its rollback, and how you verified recovery.
    ```

    ??? info "Optional: use the escalation and communications document"
        If your facilitator uploaded the knowledge files, ask:

        ```text
        Using our escalation and communications document, give the severity,
        who to notify, and a draft stakeholder email for this incident. Do not
        send or create anything.
        ```

        The company, contacts and templates are fictitious demo data. Check that
        the facts in the draft come from this incident's evidence, not from the
        document's worked example.
3. Check the agent's claims in the portal:
    * PostgreSQL still shows **Ready**, with public access disabled.
    * `PostgreSqlFaultInjection` now shows `Allow`; its other settings are
      unchanged.
    * **Activity log** shows two writes to the rule: the fault injection, then
      the agent's restore under its managed identity.
    * No VM, PostgreSQL, or other NSG rule was changed.
4. Compare the explanation with the evidence. The deliberate API restart closes
   old connections; the denied network rule caused the outage. Note
   one agent claim you checked, and the result. Mark any claim the evidence does
   not support.

??? info "Optional stretch: audit the autonomy boundary"
    The agent may change only this one rule. Ask:

    ```text
    List every Azure write you made for this incident and every action you chose
    not to take, such as restarting orders-api or the VM. Explain why each was
    out of scope.
    ```

    Compare the answer with the Activity log. Note how the narrow change and its
    recorded rollback keep an autonomous fix safe.

### Task 6: Confirm recovery

1. Note the time of the agent's rule restore. In the Orders GUI, select
   **Refresh status** and **Refresh orders**. All three status cards should recover.
2. Open the order you saved in Task 2 and check its product and quantity.
3. Check that the latest database calls succeed and wait for the alert to
   resolve. Old failures remain visible in the chart; they are incident
   history.
4. Open the network security group's **Outbound security rules** again and check
   that `PostgreSqlFaultInjection` is back to `Allow`.
5. Note the time the app looked healthy again.
6. If the agent has not restored the rule, or telemetry has not recovered after
   a reasonable wait, ask it why in the thread. As a last resort, select
   **Reset incident** in the launcher, or use the terminal reset below. If reset
   fails, check the Orders GUI and use
   [fault troubleshooting](../30-appendix/02-troubleshooting.md#fault-controls)
   or ask your facilitator. Leave no database fault active.

??? info "Terminal fallback reset"
    Use this only if the agent did not recover the app. With the
    [prepared terminal](../30-appendix/01-variables.md#terminal-setup-for-an-assigned-environment):

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
    cause, the evidence, the change you made, and the recovery step.
    ```

2. Check each draft against what you observed:
    * The fault, alert, agent restore, and recovery times match the alert history
      and your notes.
    * The cause and impact match what you verified in Task 5.
    * It contains no credentials, tokens, or real customer data.
3. Confirm nothing was sent or created. The classroom setup binds no GitHub
   repository or Outlook recipient, so the agent should say what is missing.
   Publishing would need a separate request and your approval.
4. Edit one sentence you would change before sharing the draft. Keep the drafts
   with your notes.

## Check before moving on

* [ ] Only the documented rule's access changed, and the agent made that change.
* [ ] The PostgreSQL alert reached `orders-incident-workflow` and
      `orders-alert-investigator`, running in Autonomous mode.
* [ ] Any HTTP 5xx alert remained on the original response plan.
* [ ] You traced the agent's observe, hypothesize, confirm, act, and verify steps
      and checked its claims against the rule and app.
* [ ] Access is `Allow`, the app is healthy, and your order is unchanged.
* [ ] You reviewed the follow-up drafts, and nothing was sent.

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

??? question "Why is it safe to let this agent fix the rule without approval?"
    Its autonomy is bounded: one Azure change to one named rule, a recorded
    rollback, no restarts, and verification from fresh telemetry. The original
    plan stays in Review for everything else.

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
