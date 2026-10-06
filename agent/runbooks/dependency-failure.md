---
title: Runbook - PostgreSQL dependency failure
description: Investigation procedure for Orders API failures caused by the private PostgreSQL dependency path.
ms.date: 2026-09-25
ms.topic: reference
---

Use this runbook when `alert-orders-postgresql-connectivity` fires, readiness
fails, or order operations return errors while the process remains live.

## Triage

1. Record alert fire time, severity, resource group, and investigation window.
2. Confirm VM heartbeat and `GET /health/live`. This rules out process loss but
   does not prove database health.
3. Query PostgreSQL dependencies. Identify the first failure, affected operation
   names, result codes, duration, and whether the change is a cliff or a trend.
4. Compare `orders-api-postgresql` availability and `/health/ready` observations
   with request failures over the same bins.
5. Check VM Percentage CPU. Normal CPU helps rule out the CPU exercise as the
   primary trigger.
6. Inspect the Azure Activity log and current configuration for
   `nsg-orders-<suffix>/securityRules/PostgreSqlFaultInjection`. The declared
   state is `Allow`; `Deny` is the bounded workshop fault.
7. For the bounded fault, expect an API-only Run Command after the `Deny`
   read-back. It drains stateful Npgsql sessions and verifies controlled 503
   responses. Treat it as fault delivery, not the root cause; confirm the VM,
   PostgreSQL server, IMDS, private DNS, and unrelated egress remained healthy.
8. If the rule is already `Allow`, investigate private DNS, TCP 5432, TLS,
   managed-identity authentication, server state, and schema migration in that
   order. State which checks require authorized in-VNet evidence.
9. Classify the NSG change or other demonstrated boundary as the trigger. Treat
   no circuit breaker, one VM, no database HA, and privileged runtime identity
   only as relevant contributing factors, not automatic causes.

## Reference queries

PostgreSQL dependency failures:

```kusto
AppDependencies
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api" and DependencyType == "PostgreSQL"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize
    Calls = sum(Samples),
    Failures = sumif(Samples, Success == false),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by bin(TimeGenerated, 1m), Name, Target, ResultCode
| extend FailureRate = round(100.0 * todouble(Failures) / todouble(Calls), 1)
| order by TimeGenerated asc
```

Availability:

```kusto
AppAvailabilityResults
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api" and Name == "orders-api-postgresql"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize Checks = sum(Samples), Failed = sumif(Samples, Success == false)
    by bin(TimeGenerated, 1m), Message
| order by TimeGenerated asc
```

Customer-visible request impact:

```kusto
AppRequests
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api"
| extend Samples = tolong(coalesce(ItemCount, 1))
| summarize
    Requests = sum(Samples),
    Failed = sumif(Samples, Success == false),
    P95Ms = round(percentile(DurationMs, 95), 1)
  by bin(TimeGenerated, 1m), Name, ResultCode
| extend FailureRate = round(100.0 * todouble(Failed) / todouble(Requests), 1)
| order by TimeGenerated asc
```

Sanitized provider failures:

```kusto
AppExceptions
| where TimeGenerated > ago(1h)
| where AppRoleName == "orders-api"
| where Properties["database.operation"] != ""
| summarize Occurrences = sum(tolong(coalesce(ItemCount, 1)))
    by ProblemId,
       Operation = tostring(Properties["database.operation"]),
       ResultCode = tostring(Properties["postgresql.result_code"]),
       Transient = tostring(Properties["postgresql.transient"])
| order by Occurrences desc
```

## Mitigation

For the workshop NSG incident, recommend that an authorized operator run:

```text
python scripts/workshop.py fault reset-postgresql
```

The action must restore only the named TCP 5432 rule to `Allow`, read it back,
restart only `orders-api`, and verify liveness, readiness, and an order read
before reporting success. It must not change the independent CPU fault. This
recycle is required because an NSG update does not terminate existing TCP
flows. Verify mitigation using the command result plus new successful
PostgreSQL dependencies and availability. Telemetry recovery can lag the
data-plane verification.

If evidence identifies another boundary, restore that boundary's declared
configuration rather than broadening access. Do not stop, recreate, or expose
PostgreSQL to correct a scoped NSG denial.

## Follow-up candidates

* Separate PostgreSQL bootstrap and runtime identities with least privilege.
* Add a circuit breaker and carefully bounded retry/backoff policy.
* Define degraded behavior instead of treating every read as dependent on a live
  database.
* Add a synthetic private-path probe that distinguishes DNS, TCP, TLS, auth, and
  schema failures.
* Evaluate VM and PostgreSQL high availability against cost and recovery targets.

Each recommendation must include its trade-off and a verification method.
