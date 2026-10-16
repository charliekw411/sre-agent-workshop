---
title: Contoso Retail - Incident escalation and communications
description: Fictitious demo severity definitions, contacts, escalation paths and message templates for the Contoso Orders API.
ms.date: 2026-10-16
ms.topic: reference
---

> **Fictitious demo data.** Contoso Retail, its people, teams, addresses and
> response targets are invented to demonstrate static knowledge in the
> workshop. Addresses use the reserved `example.com` domain. Draft messages
> only: never send email, create issues or contact anyone without explicit
> human approval.

## Organisation

| Team | Responsibility | Mailbox |
| --- | --- | --- |
| Platform Reliability | Owns the Orders API, alerts and on-call | `platform-reliability@example.com` |
| Data Services | Owns the PostgreSQL Flexible Server and its network | `data-services@example.com` |
| Customer Operations | Handles customer contact and order queries | `customer-ops@example.com` |
| Engineering Management | Business impact decisions and executive updates | `eng-management@example.com` |

## Severity matrix

| Severity | Meaning | Orders API examples | Acknowledge within | Update cadence |
| --- | --- | --- | --- | --- |
| Sev1 | Customers cannot place or read orders, or data is at risk | PostgreSQL unreachable; sustained HTTP 5xx | 5 minutes | Every 15 minutes |
| Sev2 | Service is degraded but customers can still order | VM CPU above 80 percent; P95 above 1,000 ms | 15 minutes | Every 30 minutes |
| Sev3 | Minor or single-feature impact, workaround exists | One slow endpoint; a failed synthetic check that recovers | 4 hours | Daily |

Rules:

* Use the severity of the Azure Monitor alert as the starting point. Raise it
  if customer impact is worse than the alert suggests. Lower it only with the
  incident commander's agreement.
* Several alerts from one cause are one incident. Use the earliest alert as the
  correlation key.

## Roles

| Role | Who (demo) | Duty |
| --- | --- | --- |
| Incident commander | On-call Platform Reliability engineer | Owns the incident, severity and decisions |
| Technical lead | Data Services on-call (database causes) or Platform Reliability (all others) | Leads diagnosis and recovery |
| Communications lead | Customer Operations duty manager | Sends customer and stakeholder updates |
| Executive sponsor | Engineering Management | Informed for Sev1; approves customer-wide statements |

## On-call rota (demo)

| Week | Platform Reliability primary | Platform Reliability secondary | Data Services on-call |
| --- | --- | --- | --- |
| Odd ISO weeks | Alex Rivera | Priya Nair | Sam Okafor |
| Even ISO weeks | Priya Nair | Jordan Lee | Mei Tanaka |

Handover is Monday 09:00 New Zealand time. Page the secondary if the primary
has not acknowledged within the acknowledgement time.

## Escalation path

| Severity | Who to notify | When |
| --- | --- | --- |
| Sev1 | Platform Reliability primary and secondary; Data Services on-call if the database or its network is involved; Customer Operations duty manager | Immediately |
| Sev1 | Engineering Management | If not mitigated within 30 minutes, or any customer data is at risk |
| Sev2 | Platform Reliability primary | Immediately |
| Sev2 | Platform Reliability secondary | If not acknowledged in 15 minutes or not mitigated in 60 minutes |
| Sev3 | Platform Reliability queue | Next business day |

Network changes to the database subnet or its security rule always involve
Data Services before anything is changed.

## Communication channels

| Purpose | Channel |
| --- | --- |
| Live coordination | Chat channel `#orders-incident` |
| Stakeholder updates | Email to the notification list for the severity |
| Tracking and follow-up | GitHub issue in the `contoso-retail/orders-platform` repository, label `incident` |
| Customer-facing status | Status page, written by the communications lead |

## Templates

Replace every `<placeholder>`. Write times in UTC with New Zealand time in
parentheses. State facts the evidence supports; mark everything else as a
hypothesis.

### GitHub issue

```markdown
Title: [Sev<1|2|3>] <alert rule> - <short customer-impact summary>

## Summary
<One or two sentences: what failed, who is affected, current status.>

## Timeline (UTC, New Zealand time in parentheses)
- <time> Fault began (evidence: <query or log>)
- <time> Alert fired: <alert rule>
- <time> Investigation started
- <time> Mitigation applied / recovery confirmed

## Customer impact
<What customers could not do, for how long, how many requests failed.>

## Evidence
- <Claim> - <query, metric or log, with time window>

## Cause
Confirmed: <fact> / Hypothesis: <hypothesis and confidence>

## Mitigation and rollback
<Smallest reversible step, how to roll it back, how recovery is validated.>

## Follow-up actions
- [ ] <action> - owner <team> - due <date>

Labels: incident, sev<1|2|3>
```

### Stakeholder email

```text
To: <notification list for the severity>
Subject: [Sev<1|2|3>] Orders API - <status: Investigating | Mitigated | Resolved>

Status: <Investigating | Mitigated | Resolved> as of <time UTC (NZ time)>

What is happening:
<Plain-language summary of the customer impact.>

What we know:
- <Evidence-backed fact>

What we do not know yet:
- <Open question>

What we are doing:
- <Current action and owner>

Next update by: <time UTC (NZ time)> (every <15|30> minutes for Sev<1|2>)

Incident commander: <name>
```

### Status update (chat)

```text
[Sev<n>] <alert rule> | <Investigating|Mitigated|Resolved> | <time UTC (NZ time)>
Impact: <one line>   Cause: <confirmed | suspected>   Next update: <time>
```

### Post-incident summary

```markdown
## Post-incident summary: <incident title>
Duration: <start> to <end> UTC (<minutes> minutes)
Detected by: <alert | scheduled health check | customer report> after <minutes> minutes
Impact: <customers affected, failed requests, SLO or error-budget effect>
Cause: <confirmed cause>
What went well: <items>
What to improve: <items, each with an owner>
```

## Worked example (fictitious past incident)

Contoso Retail incident INC-2041, filled in from the templates above.

**GitHub issue title:** `[Sev1] alert-orders-postgresql-connectivity - Customers
cannot view or place orders`

* Fault began 2026-09-02 02:14 UTC (14:14 NZST); alert fired 02:19 UTC (14:19
  NZST); recovery confirmed 02:41 UTC (14:41 NZST), 27 minutes in total.
* Customer impact: database-backed requests returned HTTP 503 for 27 minutes
  while `GET /health/live` stayed HTTP 200.
* Cause (confirmed): an outbound network security rule from the orders subnet
  to the database subnet on port 5432 was changed to Deny. Data Services
  restored the rule to Allow; Platform Reliability restarted `orders-api` to
  close stale connections.
* Escalation followed: Platform Reliability primary and secondary and the Data
  Services on-call were paged at once; Engineering Management was not needed
  because recovery took under 30 minutes.
* Follow-up: add a change-control check on the database network rule (owner
  Data Services); add a connectivity probe to the deployment pipeline (owner
  Platform Reliability).

## Using this document

* Pick the severity, owners and notification list from the tables above, and
  name this document when you use them.
* Fill the templates with evidence from the current investigation. Do not copy
  the worked example's facts into a real incident.
* Produce drafts only. A person approves and sends every issue and email.
