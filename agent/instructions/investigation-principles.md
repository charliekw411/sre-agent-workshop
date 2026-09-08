---
title: Investigation principles for Contoso Order Services
description: Testable reasoning rules for Azure SRE Agent, each derived from a specific misdiagnosis observed during the workshop investigations.
ms.date: 2026-09-08
ms.topic: reference
---

Every rule below exists because an investigation went wrong without it. Apply them to every incident in this resource group.

## Timing

1. Report the degradation start time derived from telemetry, not the alert fire time. Find the first time bin where a golden signal deviates meaningfully from the preceding baseline, and state both times plus the difference.
2. State the detection latency explicitly as its own number. It is an actionable metric, not a footnote.
3. When correlating a change with an incident, state the gap in minutes and explain the mechanism that would account for a delay. A change 90 minutes before the first symptom needs a delayed trigger to be credible.

## Latency attribution

1. When client-side dependency duration increases, compare it against the callee's server-side request duration over the same window before attributing latency to the dependency.
2. If server-side duration is flat while client-side duration rises, the additional time is caller-side queueing. The dependency is not the cause. Report the caller as saturated.
3. Report the difference between client-side and server-side duration as an explicit queueing figure rather than describing the dependency as slow.

## Failure classification

1. Distinguish a slow dependency from a fast-failing dependency. Report the failure duration. Sub-100-millisecond failures indicate refusal, not overload, and call for a different mitigation.
2. Distinguish saturation failures from hard failures by the shape of the success rate curve. A gradual slope indicates queueing. A cliff indicates rejection.
3. Always segment failure rate by operation name before aggregating. Report the worst affected operation, not the service average.

## Causal reasoning

1. Separate every finding into trigger, contributing factor, or explicitly ruled out. Provide evidence for each classification, including the exclusions.
2. Identify contributing factors by reasoning about what is absent as well as what is present. Constant dependency call volume during a sustained failure indicates no circuit breaker. Absence of replica restarts during total request failure indicates the health probe does not reflect dependency health.
3. Never present correlation as causation. If two signals moved together, say so, and state what additional evidence would establish direction.
4. State the leading hypothesis together with the evidence that would disprove it.

## Evidence discipline

1. Attach a specific query or metric reference to every factual claim.
2. Search `ContainerAppConsoleLogs_CL` for lines containing `FAULT INJECTED` early in every investigation in this resource group.
3. Check both the Azure Activity log and the Container Apps revision list when looking for correlated changes. Neither alone is complete.
4. When telemetry needed for a conclusion does not exist, say so explicitly and record it as an observability gap rather than inferring silently.

## Impact reporting

1. Express impact using the business impact model in the architecture context. `POST /orders` failures are revenue-affecting and rank above `GET /orders` failures.
2. Describe a failure that affects one operation as a partial outage of that operation, never as a total service outage.
3. Quantify impact as a rate and a duration, not as an adjective.

## Recommendations

1. Distinguish mitigation from remediation in every recommendation. State plainly whether an action restores service or removes the cause.
2. Rank remediation items by risk reduced relative to effort.
3. For every recommendation state the behavior change, the trade-off it introduces, and how the change would be verified.
4. Reject recommendations that only raise a limit without addressing consumption. Pair any quota increase with an investigation of what consumed the quota.

## Revision

1. When presented with evidence that contradicts a stated conclusion, revise the conclusion and explain what the original evidence was actually measuring. Do not restate the original claim with added confidence.
