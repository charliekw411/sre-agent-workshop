---
title: Module 02 - Observe a Healthy Baseline
description: Generate normal Orders API traffic and connect VM metrics, Application Insights views, and Log Analytics signals into one visual baseline.
ms.date: 2026-09-24
ms.topic: how-to
keywords:
  - azure monitor
  - application insights
  - log analytics
  - performance baseline
estimated_reading_time: 16
---

<ul class="sre-meta">
<li class="duration">Estimated time: 30 minutes</li>
<li>Module 02</li>
<li>Hands-on</li>
</ul>

## Overview

An incident graph is useful only when you know what normal looks like. This
module sends predictable traffic through the public API and then follows the
same activity through the VM Metrics blade, Application Insights, and Log
Analytics.

The workshop collects two complementary kinds of telemetry:

* Azure platform metrics, including the VM's **Percentage CPU**.
* Guest and application telemetry, including `Perf`, `AppRequests`,
  `AppDependencies`, `AppAvailabilityResults`, and `AppExceptions`.

The Azure Monitor Agent samples guest performance every 60 seconds. Application
Insights records requests and SQLite dependencies from the API. A background
availability probe checks that the SQLite schema remains readable once per
minute.

## Learning objectives

* Generate a stable request stream against the public API.
* Use the VM's Metrics blade to establish a visual CPU baseline.
* Observe live and aggregated request behavior in Application Insights.
* Confirm that required telemetry reaches Log Analytics.
* Record normal traffic, latency, errors, saturation, and disk capacity.

## Signal map

| Question | Portal view | Telemetry |
| --- | --- | --- |
| Is the VM using unusual CPU? | VM > Monitoring > Metrics | `Percentage CPU` |
| Are requests arriving now? | Application Insights > Live Metrics | Live request rate |
| Which endpoint is slow or failing? | Application Insights > Performance / Failures | `AppRequests` |
| Is SQLite healthy? | Application Insights and Logs | `AppDependencies`, `AppAvailabilityResults` |
| Is the data filesystem filling? | Log Analytics > Logs | `Perf`, `% Free Space` |

## Tasks

### Task 1: Confirm a healthy starting state

=== "Bash"

    ```bash
    source .workshop/workshop.env
    python scripts/workshop.py smoke
    python scripts/workshop.py fault status
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    python scripts/workshop.py smoke
    python scripts/workshop.py fault status
    ```

The CPU and disk fault states should both be inactive. If not, reset them before
creating a baseline:

=== "Bash"

    ```bash
    python scripts/workshop.py fault reset
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py fault reset
    ```

### Task 2: Start normal API traffic

Run the load generator in a separate terminal and leave it running while you
use the portal:

=== "Bash"

    ```bash
    source .workshop/workshop.env
    ./scripts/generate-load.sh "${SERVICE_ORDERS_API_ENDPOINT_URL}" 5 900
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    $until = (Get-Date).AddMinutes(15)
    $products = 'SKU-1001','SKU-1002','SKU-1003','SKU-1004','SKU-1005'
    while ((Get-Date) -lt $until) {
      $body = @{
        customerId = "baseline-$((Get-Random -Maximum 500))"
        productId  = $products | Get-Random
        quantity   = Get-Random -Minimum 1 -Maximum 6
      } | ConvertTo-Json
      Invoke-RestMethod `
        -Method Post `
        -Uri "$env:SERVICE_ORDERS_API_ENDPOINT_URL/orders" `
        -ContentType 'application/json' `
        -Body $body | Out-Null
      Start-Sleep -Milliseconds 200
    }
    ```

The Bash script targets five order-creation requests per second for 15 minutes
and prints response counts every 25 requests. The PowerShell loop targets the
same cadence, although request latency can lower its achieved rate. All data is
synthetic.

### Task 3: Watch the VM CPU chart

In the Azure portal, open your workshop VM and select **Monitoring** >
**Metrics**. Configure:

* Metric: **Percentage CPU**
* Aggregation: **Average**
* Time granularity: **1 minute**
* Time range: **Last 30 minutes**

Keep the chart open for at least five samples while the load generator runs.
Hover over the graph and record the typical range. Save or screenshot this chart;
it is the visual comparison for Module 04.

<!-- SCREENSHOT: VM Percentage CPU chart showing the steady healthy-load baseline -->

!!! important "Use the VM chart for the incident comparison"
    Guest `Perf` data is useful for KQL and correlation, but the most direct visual
    CPU signal for this workshop is the VM's own **Monitoring > Metrics** blade.

### Task 4: Watch requests in Application Insights

From the workshop resource group, open `appi-<suffix>`.

1. Select **Live Metrics** and confirm requests arrive while the generator runs.
2. Select **Investigate** > **Performance** and open the `POST /orders`
   operation.
3. Select **Investigate** > **Failures** and confirm there is no sustained
   server-failure pattern.

Live Metrics is the immediate view. Performance and Failures depend on ingestion
and can lag by several minutes. Use **Last 30 minutes** consistently when
comparing portal views.

<!-- SCREENSHOT: Application Insights Live Metrics showing Orders API request traffic -->

### Task 5: Confirm the telemetry contract

=== "Bash"

    ```bash
    python scripts/workshop.py telemetry
    ```

=== "PowerShell"

    ```powershell
    python scripts/workshop.py telemetry
    ```

The command waits up to ten minutes for at least one sample of:

* VM heartbeat
* Guest CPU
* `/var/lib/orders` free space
* API requests
* SQLite dependencies
* SQLite availability results

Exceptions are intentionally not required for a healthy baseline.

### Task 6: Build a visual Log Analytics baseline

Open the Log Analytics workspace `law-<suffix>`, select **Logs**, set the time
range to **Last 30 minutes**, and run:

```kusto
AppRequests
| where AppRoleName == "orders-api"
| summarize
    Requests = sum(ItemCount),
    P95Ms = percentile(DurationMs, 95),
    Failures = sumif(ItemCount, Success == false)
  by bin(TimeGenerated, 1m)
| render timechart
```

Then visualize guest CPU:

```kusto
Perf
| where ObjectName == "Processor"
| where CounterName == "% Processor Time"
| where InstanceName == "_Total"
| summarize AverageCpu = avg(CounterValue) by bin(TimeGenerated, 1m)
| render timechart
```

Finally, visualize the SQLite filesystem:

```kusto
Perf
| where ObjectName == "Logical Disk"
| where CounterName == "% Free Space"
| where InstanceName == "/var/lib/orders"
| summarize AverageFreeSpace = avg(CounterValue) by bin(TimeGenerated, 1m)
| render timechart
```

Use the **Chart** view for each result. Pinning the charts to a private Azure
dashboard is optional, but keeping the same time range makes the later incident
shapes easier to compare.

### Task 7: Record the baseline

Create `.workshop/notes/baseline.md` and record:

| Signal | Healthy value |
| --- | --- |
| VM average CPU | |
| `POST /orders` P95 duration | |
| Request success rate | |
| SQLite availability | |
| `/var/lib/orders` free space | |
| Active Azure Monitor alerts | |

Check the current alerts in the portal under **Monitor** > **Alerts**, filtered
to the workshop resource group. A fresh healthy baseline should have no active
alerts.

Stop the generator with ++ctrl+c++ after you have captured at least ten minutes
of traffic.

## Validation

* [x] The load generator reports successful order creation.
* [x] The VM Metrics blade shows a stable CPU baseline.
* [x] Application Insights Live Metrics shows current requests.
* [x] Performance identifies `POST /orders` as an operation.
* [x] `python scripts/workshop.py telemetry` succeeds.
* [x] Log Analytics renders request, CPU, and data-disk timecharts.
* [x] The baseline table is complete.

## Knowledge check

??? question "Why use both VM Percentage CPU and the Perf table?"
    The VM metric is the direct platform visualization and drives the CPU alert. The guest performance counter lands in Log Analytics, where it can be correlated with requests, dependencies, and disk counters in KQL. They describe the same resource through different analysis paths.

??? question "Why is the SQLite availability result not an external uptime check?"
    The probe runs inside the Orders API VM and tests local database readability. It can detect a database problem while the process is alive, but it cannot detect loss of the public network path or failure of the entire VM because no external system is making the request.

??? question "Why capture a baseline before triggering any fault?"
    A value such as 40 percent CPU or 300 ms P95 has no meaning without context. The baseline turns later observations into measurable deltas and prevents normal ingestion variation from being mistaken for an incident.

## Next steps

[Next: Module 03 - Operate the SRE Agent Response Plan :material-arrow-right:](../03-operate-response-plan/index.md){ .md-button .md-button--primary }

<div class="sre-nav" markdown>
[:material-arrow-left: Module 01 - Deploy and Validate](../01-deploy-and-validate/index.md)
[Module 03 - Operate the SRE Agent Response Plan :material-arrow-right:](../03-operate-response-plan/index.md)
</div>
