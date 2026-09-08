---
title: Runbook - capacity and quota exhaustion
description: Investigation procedure for storage, quota, and connection limit exhaustion, including how to use leading indicators.
ms.date: 2026-09-08
ms.topic: reference
---

Use this runbook when a resource approaches or reaches a configured limit.

## Signals that select this runbook

* `storage_percent` or a comparable utilization metric trending upward over minutes or hours.
* A specific error code indicating a limit, such as SQL error 40544 for database size quota.
* Write operations failing while read operations succeed.
* A failure rate that is total within one operation and modest in aggregate.

## Why this class is different

Capacity incidents have a leading indicator. Utilization climbs measurably before anything fails, which creates a window to act. Detection during that window converts an outage into a maintenance task. Always report the width of that window: the interval between the threshold alert firing and the first customer-visible failure.

## Procedure

1. Identify the limit that was reached and the exact configured value, not just the utilization percentage.
2. Reconstruct the growth curve. Report the rate of growth and, if possible, when the limit would have been reached at that rate.
3. Report the interval between the threshold alert and the first failure.
4. Segment failures by operation. Capacity exhaustion on a data store typically produces a write-path outage with an unaffected read path.
5. Correlate the growth onset with deployments, configuration changes, traffic changes, and scheduled jobs.
6. Determine whether the growth is legitimate business growth or anomalous consumption. The correct remediation differs entirely between the two.

## Reference queries

Segmented failure rate, which reveals the partial outage:

```kusto
AppRequests
| where TimeGenerated > ago(2h)
| where AppRoleName == "orders-api"
| summarize
    Total = count(),
    Failed = countif(Success == false),
    FailureRate = round(100.0 * countif(Success == false) / count(), 1)
  by bin(TimeGenerated, 5m), Name
| order by TimeGenerated asc, Name asc
```

Platform error detail:

```kusto
AzureDiagnostics
| where TimeGenerated > ago(2h)
| where ResourceProvider == "MICROSOFT.SQL"
| where Category in ("Errors", "SQLInsights")
| project TimeGenerated, Category, error_number_d, Message
| order by TimeGenerated desc
```

Application-side exception signature:

```kusto
AppExceptions
| where TimeGenerated > ago(2h)
| summarize Occurrences = count(), FirstSeen = min(TimeGenerated) by AppRoleName, ProblemId, OuterMessage
| order by FirstSeen asc
```

## Mitigations

| Mitigation                    | Restores service | Addresses cause | Notes                                                     |
|-------------------------------|------------------|-----------------|-----------------------------------------------------------|
| Raise the limit               | Yes              | No              | Safest during an incident; requires follow-up on consumption |
| Delete or archive data        | Yes              | Partially       | Risky during an incident; data loss is irreversible         |
| Shed or defer write traffic   | Partially        | No              | Buys time when raising the limit is slow                    |

Prefer raising the limit during an incident. Deleting production data under time pressure is how an incident becomes a disaster.

## Remediation candidates

* Retention or archival policy matched to actual access patterns.
* Growth rate monitoring with a forecast alert, not only a static utilization threshold.
* Capacity review cadence tied to the forecast.
* Identification and correction of whatever produced anomalous growth, if the growth was not legitimate.

Never close a capacity incident with only a limit increase. The same incident recurs at the new limit on a predictable schedule.
