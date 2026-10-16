---
title: Contoso Retail - Orders API SLIs, SLOs and baselines
description: Fictitious demo service-level objectives and baseline measurements for the Contoso Orders API.
ms.date: 2026-10-16
ms.topic: reference
---

> **Fictitious demo data.** Contoso Retail, its targets and every number below
> are invented to demonstrate static knowledge in the workshop. They are not
> measured from your environment. Live telemetry always wins: when a query
> disagrees with this document, report the query result and say that it differs
> from the documented baseline.

## Service

Contoso Retail runs the **Orders API** (`orders-api`) on a single Linux VM with
a private Azure Database for PostgreSQL Flexible Server. Customers place and
read orders through the web front end. The Platform Reliability team owns the
service objectives below.

## Service level indicators (SLIs)

| SLI | Definition | Source |
| --- | --- | --- |
| Availability | Requests that did not fail (`Success == true`) divided by all requests, excluding `GET /health/live` | `AppRequests` |
| Latency | P50 and P95 of `DurationMs` for database-backed operations | `AppRequests` |
| Database dependency success | Successful PostgreSQL calls divided by all PostgreSQL calls | `AppDependencies` where `DependencyType == "PostgreSQL"` |
| Synthetic availability | Passing `orders-api-postgresql` checks divided by all checks | `AppAvailabilityResults` |
| CPU saturation | Average guest `% Processor Time` over 5 minutes | `Perf` |
| Heartbeat continuity | Longest gap between VM heartbeats | `Heartbeat` |

## Service level objectives (SLOs)

| SLO | Target | Window |
| --- | --- | --- |
| Availability | 99.5 percent | 30 days |
| P95 latency | Under 1,000 ms | Rolling 15 minutes |
| P50 latency | Under 250 ms | Rolling 15 minutes |
| Database dependency success | 99.9 percent | 30 days |
| Synthetic availability | 99.9 percent | 30 days |
| CPU saturation | Average under 80 percent | Rolling 5 minutes |
| Heartbeat continuity | No gap longer than 5 minutes | Rolling 15 minutes |

### Error budget

A 99.5 percent availability target over 30 days allows 0.5 percent failed
requests, or about 3 hours 36 minutes of full outage.

| Budget consumed | Meaning | Action |
| --- | --- | --- |
| Under 50 percent | Healthy | Normal change rate |
| 50 to 100 percent | At risk | Review recent changes; avoid risky releases |
| Over 100 percent | Exhausted | Freeze non-essential changes; prioritise reliability work |

## Alert thresholds

| Alert rule | Severity | Condition |
| --- | --- | --- |
| `alert-orders-high-cpu` | Sev2 | Average VM CPU above 80 percent for 5 minutes |
| `alert-orders-postgresql-connectivity` | Sev1 | Any failed PostgreSQL dependency in 5 minutes |
| `alert-orders-http-5xx` | Sev1 | More than 10 HTTP 5xx responses in 5 minutes |

## Baselines (demo values)

Measured over a typical fictitious fortnight with no incidents. Times are New
Zealand time (Pacific/Auckland).

### Traffic and latency

| Period | Requests per minute | P50 (ms) | P95 (ms) | Failure rate |
| --- | --- | --- | --- | --- |
| Weekday 09:00-17:00 | 40 to 70 | 60 to 90 | 220 to 380 | Under 0.1 percent |
| Weekday 17:00-22:00 | 25 to 45 | 55 to 80 | 200 to 320 | Under 0.1 percent |
| Overnight 22:00-07:00 | 2 to 8 | 50 to 70 | 180 to 260 | Under 0.1 percent |
| Weekend daytime | 15 to 30 | 55 to 80 | 200 to 300 | Under 0.1 percent |

### Infrastructure

| Measure | Normal range | Notes |
| --- | --- | --- |
| VM CPU, average | 8 to 22 percent | Peaks to about 35 percent during the daily 02:00 batch |
| VM CPU, one-minute peak | Under 45 percent | Anything sustained above 80 percent is abnormal |
| PostgreSQL calls per minute | 1.2 to 1.6 times the request rate | Each order read or write makes one to two calls |
| PostgreSQL call P95 | 15 to 40 ms | Rises with CPU saturation on the VM, not only with database load |
| Heartbeats | One per minute | A gap of 2 minutes or more is worth noting |
| Synthetic check | Passes every 5 minutes | Fails together with dependency errors, not before them |

### Typical incident signatures

| Signal | High CPU on the VM | PostgreSQL unreachable |
| --- | --- | --- |
| CPU | Above 80 percent | Normal |
| `GET /health/live` | HTTP 200 | HTTP 200 |
| `GET /health/ready` | HTTP 200, slower | HTTP 503 |
| Request failures | Rare; latency rises first | Database-backed requests fail |
| PostgreSQL call duration | Rises with CPU | Calls fail, so duration is not meaningful |
| Alert | `alert-orders-high-cpu` (Sev2) | `alert-orders-postgresql-connectivity` (Sev1), often `alert-orders-http-5xx` |

## Status rubric

Use this rubric for every health summary and report. Current status uses only
the **last 15 minutes**, whatever time range is being reviewed.

| Status | Rule |
| --- | --- |
| No data | No samples in the last 15 minutes. Never report Healthy without samples. |
| Unhealthy | Any failed request, PostgreSQL dependency call or availability check; average guest CPU above 80 percent; or a heartbeat gap longer than 5 minutes |
| Degraded | No failures, but P95 duration above 1,000 ms |
| Healthy | Samples exist and none of the above applies |

* A failure earlier in the review window that has recovered is a **window
  finding**, not the current status.
* A missing baseline is a gap to state, not a reason to assume Healthy.
* Compare observations with the baselines above and say how far outside the
  normal range they are, for example "CPU 92 percent against a normal 8 to 22
  percent".

## Using this document

* Quote the SLO or baseline you compared against, and name this document.
* Do not present a demo baseline as a measurement of the live environment.
* If an observed value is within the baseline range, say it is normal rather
  than inventing a cause.
