---
title: Module 03 - Respond to PostgreSQL Connectivity Loss
description: Block the app's private database connection, watch the agent investigate, and restore access without changing orders.
ms.date: 2026-10-14
ms.topic: how-to
keywords:
  - postgresql
  - network fault
  - azure sre agent
  - incident response
estimated_reading_time: 8
---

<ul class="sre-meta">
<li class="duration">Estimated time: 30 minutes</li>
<li>Module 03</li>
<li class="incident">Incident 2 of 2</li>
</ul>

## Overview

Block the connection between your Orders VM and PostgreSQL. Watch how the
customer app, alert, and dedicated incident investigator respond. Then restore
the connection and check that your order has not changed.

## What changes

The fault changes one network security group (NSG) rule:

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

In your SRE Agent, check the two complementary response plans:

| Plan | Title contains | Handling agent |
| --- | --- | --- |
| `workshop-sev1-sev2-review` | `alert-orders-h` | Coordinating or main agent |
| `orders-incident-workflow` | `postgresql` | `orders-alert-investigator` |

Both plans must be On, handle Sev1 and Sev2, and use **Review** mode. Do not
remove either title filter; CPU and HTTP 5xx stay on the original plan while
the PostgreSQL connectivity alert uses the dedicated investigator.

In **Monitor > Alerts > Alert rules**, filter to your resource group.
`alert-orders-postgresql-connectivity` should be enabled, be Sev1, and fire
when a PostgreSQL dependency fails within five minutes.

Open your NSG's **Outbound security rules**. Find `PostgreSqlFaultInjection`
and check it against the table above. Do not change its addresses, port, or
priority.

### Task 2: Save a healthy order

Confirm the header targets your assigned environment. In the launcher below,
select **Check fault status**. CPU and PostgreSQL should be inactive, access
should be `Allow`, and application connectivity should be `ready`.

In your Orders GUI, create a synthetic order with customer ID
`postgresql-incident`. Save its order ID, product, and quantity. You will read
the same order after recovery.

Select **Refresh status** and **Refresh orders**. All three status cards and
the order list should work before injection.

### Task 3: Block the database connection

Record the start time, then select **Block PostgreSQL access**.

<div data-sre-incident="postgresql">
<p><strong>JavaScript is required for the launcher.</strong> If it is unavailable, use the terminal alternative below after your facilitator has prepared it.</p>
</div>

Success means the rule is `Deny`, the API has restarted, and the launcher has
checked the expected HTTP responses. A rule change alone is not enough.

Select **Refresh status** and **Refresh orders** in the Orders GUI:

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

Keep the Orders tab visible and refresh orders a few times. Watch failed
PostgreSQL calls appear in the launcher's graph. Missing samples are not
successful calls.

Open **Monitor > Alerts**, filtered to your resource group. Find the fired
`alert-orders-postgresql-connectivity` alert, then use **Open SRE Agent
incidents** to follow its investigation.

Confirm the PostgreSQL alert matched `orders-incident-workflow` and the
investigation used `orders-alert-investigator`. The HTTP 5xx alert may also fire
because orders return 503; it should remain on `workshop-sev1-sev2-review`.
Match the alert and timestamps even if the agent uses an existing thread.
Allow for monitoring and alert delays; do not toggle the fault to make a chart
update.

Record the first observed failure, alert time, and investigation link.

<!-- SCREENSHOT: Failed PostgreSQL calls, fired alert, and agent investigation for the same assigned environment -->

### Task 5: Check the agent's explanation

Read the initial investigation. If needed, ask:

```text
Use orders-azure-monitor-rca for this PostgreSQL connectivity alert. Confirm
the response plan and handling agent, whether the VM and database stayed
running, which network rule changed, and how requests were affected. Cite the
evidence for the cause and the smallest recovery step. Do not make changes.
```

Check its answer in the portal:

* PostgreSQL still shows **Ready**, with public access disabled.
* `PostgreSqlFaultInjection` shows `Deny`; its other settings are unchanged.
* **Activity log** shows the rule write near the fault start.
* The Orders GUI shows a running process but failed database-backed requests.

The deliberate API restart closes old connections. The denied network rule
causes the continuing outage. Save any claim the evidence does not support.

### Task 6: Restore the connection

Select **Reset incident**, then **Check fault status**. Expect PostgreSQL
inactive, access `Allow`, and application connectivity `ready`. CPU should
remain inactive.

In the Orders GUI, refresh orders and status. Open the order you saved in
Task 2 and check its product and quantity. All status cards should recover.

Check the latest database calls succeed and wait for the alert to resolve.
Old failures remain visible in the chart; they are incident history.

Save the reset and recovery times. If reset fails, check status and use
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

## Check before moving on

* [ ] Only the documented rule's access changed.
* [ ] The PostgreSQL alert reached `orders-incident-workflow` and
      `orders-alert-investigator`.
* [ ] Any HTTP 5xx alert remained on the original response plan.
* [ ] You checked the agent's explanation against the rule and app.
* [ ] Access is `Allow`, the app is healthy, and your order is unchanged.

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

## Next

[Deploy and evaluate proactive SRE checks :material-arrow-right:](../05-scheduled-health-check/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 02 - Configure SRE and Respond to High CPU](../03-incident-high-cpu/index.md)
[Module 04 - Deploy and Evaluate Proactive SRE Checks :material-arrow-right:](../05-scheduled-health-check/index.md)
</div>
