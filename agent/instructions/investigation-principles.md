---
title: Investigation principles for Contoso Order Services
description: Evidence rules for the VM and private PostgreSQL workshop.
ms.date: 2026-09-25
ms.topic: reference
---

Apply these rules to every incident in this resource group.

## Establish time and impact

1. Derive the degradation start from telemetry, not the alert fire time. State
   both times and the detection latency.
2. Quantify impact by operation, failure rate, and duration. All order operations
   use PostgreSQL, but `/`, `/health/live`, and `/health/ready` have different
   semantics and must not be combined into one success claim.
3. Treat failed readiness as a dependency-health signal and liveness as a
   process-health signal. A live process with failed readiness is expected during
   the PostgreSQL connectivity exercise.
4. Account for Application Insights ingestion delay before ordering events that
   occur only a few minutes apart.

## Distinguish failure shapes

1. A gradual latency rise with high VM CPU supports saturation. A sharp
   PostgreSQL success-rate cliff with normal VM CPU supports a connectivity
   boundary failure.
2. Segment `AppDependencies` by `Name`, `Target`, `DependencyType`,
   `ResultCode`, and `Success`. Do not label every provider failure as a server
   outage.
3. Compare request, dependency, exception, availability, heartbeat, and platform
   metric timelines over the same window.
4. A private DNS failure, TCP denial, TLS failure, token failure, missing schema,
   and stopped server can all make readiness fail. Identify which boundary the
   available evidence supports and state what remains unverified.

## Causal reasoning

1. Classify each claim as trigger, contributing factor, or ruled out.
2. Check the Azure Activity log for a write to the named NSG security rule and
   the expected subsequent API-only Run Command. The restart drains established
   pooled TCP sessions because NSGs are stateful; do not misclassify that
   deterministic delivery step as the trigger or infer a VM/database restart.
   Correlate the scoped `Deny` with the verified controlled 503 responses and
   first failed PostgreSQL dependency.
3. Confirm recovery after the rule returns to `Allow`. Temporal correlation in
   both the failure and recovery directions strengthens causality.
4. State a falsifiable leading hypothesis and the evidence that would disprove it.
5. Do not call the managed database unhealthy solely because the VM cannot reach
   it. The workshop incident intentionally leaves the server running and data
   unchanged.

## Safety and authority

1. The SRE Agent is read-only. Phrase remediation as a recommendation to an
   authorized operator, never as an action already taken.
2. Prefer restoring `PostgreSqlFaultInjection` to its declared `Allow` state.
   Do not recommend broad NSG removal, public PostgreSQL access, firewall
   exceptions, password credentials, disabled TLS verification, or excessive
   identity privilege.
3. Do not restart or recreate PostgreSQL for a scoped NSG denial. Those actions
   increase impact without addressing the demonstrated boundary.
4. Preserve orders and incident evidence. Never suggest deleting the database or
   resource group as an incident mitigation.

## Evidence discipline

1. Attach a specific query, metric, resource property, or Activity log event to
   every material claim.
2. Record absent evidence as an observability gap rather than silently filling it
   with an assumption.
3. Use exact resource and alert names from the selected workshop resource group;
   do not import conclusions from another environment.
4. Sanitize identity tokens, connection material, and exception detail. No
   credential value is required to investigate this incident.

## Recommendations

1. Separate immediate mitigation from long-term remediation.
2. For each recommendation, state the behavior change, trade-off, and verification.
3. Rank actions by risk reduced relative to effort.
4. Do not solve dependency fragility only by raising timeout or capacity.
   Consider bounded retries, circuit breaking, degraded behavior, identity
   separation, and redundancy, with their failure-mode trade-offs.

When new evidence contradicts a conclusion, revise the conclusion and explain
what the original evidence actually measured.
