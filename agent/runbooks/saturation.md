---
title: Runbook - VM CPU saturation
description: Investigation procedure for CPU saturation on the Orders API virtual machine.
ms.date: 2026-09-25
ms.topic: reference
---

Use this runbook when `alert-orders-high-cpu` fires or request latency rises while
the VM remains online.

## Procedure

1. Establish the latency degradation start from `AppRequests`, then compare it
   with the alert fire time.
2. Chart VM `Percentage CPU` at one-minute granularity. Report the peak and time
   above the 80 percent alert threshold.
3. Confirm heartbeat continuity and inspect request rate, success rate, and
   latency. CPU saturation can degrade latency before it creates exceptions.
4. Check PostgreSQL dependency success and duration. If dependency calls remain
   successful but their client-observed duration rises with CPU, caller-side
   scheduling is a more likely explanation than a slow database.
5. Check the Azure VM Run Command and transient unit evidence available to the
   authorized operator. The SRE Agent cannot run or stop the fault.
6. Rule out a simultaneous PostgreSQL NSG denial by checking dependency success
   shape and the named rule's Activity log.
7. Separate the bounded trigger from contributing factors such as one VM and no
   autoscaling.

## Reference query

```kusto
AppRequests
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize
    Requests = sum(Samples),
    Failed = sumif(Samples, Success == false),
    P50Ms = round(percentile(DurationMs, 50), 1),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by bin(TimeGenerated, 1m)
| extend SuccessRate = round(100.0 * todouble(Requests - Failed) / todouble(Requests), 1)
| order by TimeGenerated asc
```

## Mitigation and verification

An authorized operator can run `python scripts/workshop.py fault reset-cpu` to
stop only the workshop CPU unit without changing PostgreSQL access. Verify that
CPU falls, request latency returns toward baseline, heartbeat remains continuous,
and PostgreSQL dependencies are successful.

Long-term options include horizontal redundancy, autoscaling, resource limits,
load shedding, and profiling. Increasing VM size raises cost and the ceiling but
does not explain CPU consumption.

## Observability gap

Platform CPU metrics do not attribute consumption to a method or request. State
that limitation. Continuous profiling would be required for code-level
attribution outside the known workshop injection.
