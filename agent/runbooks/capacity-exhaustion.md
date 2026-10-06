---
title: Runbook - PostgreSQL capacity and limits
description: Investigation procedure for managed PostgreSQL storage, compute, connection, and quota limits.
ms.date: 2026-09-25
ms.topic: reference
---

Use this runbook only when evidence indicates a Flexible Server limit. The
workshop's Module 04 incident is a scoped network denial, not a capacity event.

## Procedure

1. Identify the exact server metric, configured limit, provider error code, and
   first affected operation.
2. Distinguish storage growth, compute saturation, connection exhaustion, and
   Azure subscription or regional capacity. They require different actions.
3. Reconstruct the trend and calculate the warning interval between the first
   threshold breach and customer-visible failures.
4. Correlate growth or utilization with request volume, migrations, deployment,
   maintenance, and configuration changes.
5. Verify the NSG rule is `Allow` before attributing connection failures to
   server capacity.
6. State whether auto-grow acted and whether provider telemetry is sufficient for
   the conclusion.

## Application impact query

```kusto
AppDependencies
| where TimeGenerated > ago(2h)
| where AppRoleName == "orders-api" and DependencyType == "PostgreSQL"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize
    Calls = sum(Samples),
    Failed = sumif(Samples, Success == false),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by bin(TimeGenerated, 5m), Name, ResultCode
| order by TimeGenerated asc
```

## Mitigation principles

Scaling compute or storage can restore service when a demonstrated limit is the
cause, but it raises cost and does not explain consumption. Do not delete orders
under incident pressure. Pair any limit increase with retention, indexing,
query, connection-pool, or workload analysis as appropriate.

The workshop server is intentionally `Standard_B1ms`, 32 GiB with auto-grow, and
no HA. Those settings are cost-oriented lab constraints. Recommend a production
tier or HA only after stating the workload and recovery objectives that justify
the change.
