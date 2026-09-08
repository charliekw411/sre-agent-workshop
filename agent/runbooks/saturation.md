---
title: Runbook - resource saturation
description: Investigation procedure for CPU, memory, and thread saturation on Azure Container Apps workloads.
ms.date: 2026-09-08
ms.topic: reference
---

Use this runbook when latency rises without a corresponding rise in exceptions, or when a saturation metric alert fires.

## Signals that select this runbook

* `UsageNanoCores` or `WorkingSetBytes` approaching the container allocation.
* Latency percentiles rising while success rate degrades gradually rather than sharply.
* Dependency durations rising across all dependencies simultaneously, which points at the caller rather than at any callee.
* No increase in `AppExceptions`.

## Procedure

1. Establish the degradation start time from `AppRequests` percentiles, not from the alert.
2. Compare the saturated resource against its allocation. Report utilization as a percentage of the limit, never as an absolute figure alone.
3. Compare the suspect service against every peer service in the same environment over the same window. A single saturated service with healthy peers narrows the cause to that service.
4. Run the client-versus-server latency comparison before blaming any dependency.
5. Check replica count and scale configuration. A service pinned to a fixed replica count cannot absorb load, and that constraint is part of the explanation.
6. Check the Activity log and the revision list for a change that altered resource allocation or scale rules.
7. Search container console logs for warnings, errors, and the `FAULT INJECTED` marker.

## Reference queries

Latency and success rate over time:

```kusto
AppRequests
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api"
| summarize
    Requests = count(),
    SuccessRate = round(100.0 * countif(Success == true) / count(), 2),
    P50Ms = round(percentile(DurationMs, 50), 1),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by bin(TimeGenerated, 2m)
| order by TimeGenerated asc
```

Caller-side queueing versus callee-side latency:

```kusto
let clientView =
    AppDependencies
    | where TimeGenerated > ago(1h)
    | where AppRoleName == "orders-api" and Type == "HTTP"
    | summarize ClientP95Ms = round(percentile(DurationMs, 95), 1) by bin(TimeGenerated, 5m);
let serverView =
    AppRequests
    | where TimeGenerated > ago(1h)
    | where AppRoleName == "catalog-api"
    | summarize ServerP95Ms = round(percentile(DurationMs, 95), 1) by bin(TimeGenerated, 5m);
clientView
| join kind=inner serverView on TimeGenerated
| project TimeGenerated, ClientP95Ms, ServerP95Ms, QueueingMs = ClientP95Ms - ServerP95Ms
| order by TimeGenerated asc
```

## Mitigations

| Mitigation                   | Restores service | Addresses cause | Notes                                                      |
|------------------------------|------------------|-----------------|------------------------------------------------------------|
| Stop the offending workload  | Yes              | Yes             | Only possible when the consumer is identifiable and stoppable |
| Scale out replicas           | Yes              | No              | Dilutes the effect; the consumer keeps running              |
| Increase CPU allocation      | Yes              | No              | Raises the ceiling without explaining consumption           |
| Shed load                    | Partially        | No              | Protects the remaining capacity at the cost of some requests |

## Known gaps in this environment

There is no per-operation CPU attribution. Metrics can show that CPU is saturated but not which code path consumed it. Container console logs and a continuous profiler are the only ways to close that gap.
