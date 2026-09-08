---
title: Runbook - dependency failure and cascade
description: Investigation procedure for downstream service failures that surface as errors on a calling service.
ms.date: 2026-09-08
ms.topic: reference
---

Use this runbook when a service returns errors while its own resource utilization is normal.

## Signals that select this runbook

* HTTP 5xx rate rising with CPU and memory at baseline.
* `AppDependencies` failure ratio elevated for a specific target.
* A repeating exception signature in `AppExceptions` originating from an outbound call.
* Read operations succeeding while write operations fail, or the reverse, depending on which path uses the dependency.

## Procedure

1. Segment `AppRequests` by operation name. Identify precisely which operations fail and which succeed.
2. Select one failed operation and follow its `OperationId` across `AppRequests`, `AppDependencies`, and `AppExceptions` to reconstruct the call chain.
3. Determine where in the chain the failure originates. The service emitting the customer-facing error is frequently not the service at fault.
4. Measure the failing dependency call duration. Sub-100-millisecond failures mean the callee refused quickly and is not overloaded. Long durations mean timeouts and caller resource exhaustion.
5. Check the callee's own health independently: server-side request duration, resource utilization, replica restarts. A fast-failing callee with normal resources is refusing deliberately, which points at its own dependency, a feature flag, or a deployment.
6. Examine the caller's behavior during the failure. Constant dependency call volume throughout a sustained failure means no circuit breaker opened.
7. Check whether any replica was removed from rotation. If none was, the health probe does not reflect dependency health.
8. Classify each finding as trigger, contributing factor, or ruled out.

## Reference queries

Failure segmentation by operation:

```kusto
AppRequests
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api"
| summarize
    Total = count(),
    Failed = countif(Success == false),
    FailureRate = round(100.0 * countif(Success == false) / count(), 1)
  by Name
| order by Failed desc
```

End-to-end trace for one operation:

```kusto
union
  (AppRequests     | extend Kind = "Request",    Detail = strcat(Name, " -> ", ResultCode)),
  (AppDependencies | extend Kind = "Dependency", Detail = strcat(Type, " ", Target, " -> ", ResultCode)),
  (AppExceptions   | extend Kind = "Exception",  Detail = strcat(ProblemId, ": ", OuterMessage), DurationMs = 0.0)
| where OperationId == "<operation-id>"
| project TimeGenerated, Kind, AppRoleName, Detail, DurationMs, Success
| order by TimeGenerated asc
```

Dependency failure ratio by target:

```kusto
AppDependencies
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api"
| summarize
    Calls = count(),
    Failures = countif(Success == false),
    FailureRate = round(100.0 * countif(Success == false) / count(), 1),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by Target, Type
```

## Mitigations

| Mitigation                        | Restores service | Addresses cause | Notes                                             |
|-----------------------------------|------------------|-----------------|---------------------------------------------------|
| Restore the failing dependency    | Yes              | Removes trigger | Leaves the caller equally fragile next time       |
| Enable a fallback or cached value | Yes              | Partially       | Requires the fallback path to already exist       |
| Open a circuit breaker manually   | Partially        | No              | Fails fast instead of failing slowly              |
| Roll back the dependency release  | Yes              | Removes trigger | Only valid when a release correlates in time      |

## Remediation candidates

* Circuit breaker with defined open and half-open thresholds.
* Fallback to a cached or last-known value.
* Explicit degraded mode, such as accepting the request for asynchronous completion.
* Retry with exponential backoff and jitter.
* Readiness signal that reflects critical dependency health with hysteresis and a guaranteed minimum capacity in rotation.

Each of these has a cost. State the trade-off alongside the recommendation.
