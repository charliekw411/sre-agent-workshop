---
title: Troubleshooting
description: Diagnose deployment, private PostgreSQL, managed identity, schema, NSG fault, telemetry, SRE Agent, scheduled health check, and cleanup problems in the workshop.
ms.date: 2026-10-12
ms.topic: troubleshooting
keywords:
  - troubleshooting
  - postgresql flexible server
  - managed identity
  - private dns
  - azure sre agent
estimated_reading_time: 20
---

## How to use this page

Start with the failed workshop command and its evidence under
`.workshop/<environment>/`. Load the selected environment:

=== "Bash"

    ```bash
    source .workshop/workshop.env
    ```

=== "PowerShell"

    ```powershell
    . ./.workshop/workshop.ps1
    ```

Do not work around a failure by opening SSH, enabling PostgreSQL public access,
adding a database password, creating a public fault endpoint, broadening the NSG
deny, or granting the SRE Agent write access.

Before diagnosing normal connectivity, check the controlled fault state:

```bash
python scripts/workshop.py fault status
```

If PostgreSQL is `active` with access `Deny` outside Module 04, restore it:

```bash
python scripts/workshop.py fault reset-postgresql
```

## Deployment and preflight

### Azure CLI and azd use different identities

Preflight compares principal and tenant claims from both token sources. Sign in
again with the same intended identity:

```bash
az login
az account set --subscription "<subscription-id>"
az account show \
  --query "{Subscription:id,Tenant:tenantId,Identity:user.name}" \
  --output table
azd auth login
azd env get-value AZURE_SUBSCRIPTION_ID
```

Do not bypass this check. Provisioning, role assignment, post-provision, and
cleanup must target the same subscription and tenant.

### A legacy resource group is rejected

The current architecture requires:

```text
workshop-architecture=single-vm-postgresql-v1
```

Inspect the selected group:

=== "Bash"

    ```bash
    az group show \
      --name "rg-sre-agent-workshop-${AZURE_ENV_NAME}" \
      --query "{Name:name,Architecture:tags.\"workshop-architecture\"}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    az group show `
      --name "rg-sre-agent-workshop-$env:AZURE_ENV_NAME" `
      --query "{Name:name,Architecture:tags.\"workshop-architecture\"}" `
      --output table
    ```

The deployment rejects a missing or different tag rather than migrating,
deleting, or combining legacy resources. Create a new environment:

```bash
azd env new "<your-alias>-sre-vm-aue"
azd env set AZURE_LOCATION australiaeast
azd up
```

Review and remove the old disposable environment separately. Do not change its
tag to bypass compatibility checks.

For a pre-created group, explicitly select it with
`azd env set WORKSHOP_RESOURCE_GROUP_NAME "<group-name>"`. Preflight accepts
this fresh target only when both its tags and resource inventory are empty.
The normal deployment applies the workshop tags. It does not adopt an occupied
or tagged legacy environment.

### A required provider is unavailable or unregistered

Preflight registers all required providers, including
`Microsoft.DBforPostgreSQL`. Check registration:

```bash
az provider show \
  --namespace Microsoft.DBforPostgreSQL \
  --query "{Namespace:namespace,State:registrationState}" \
  --output table
```

If your identity can register providers:

```bash
az provider register --namespace Microsoft.DBforPostgreSQL
```

Registration can take several minutes. Rerun `azd up` after it reaches
`Registered`. A policy or subscription type can still prevent a resource even
when the provider is registered.

### PostgreSQL 16 or `Standard_B1ms` is not advertised

Preflight calls:

```bash
az postgres flexible-server list-skus \
  --location "${AZURE_LOCATION}" \
  --subscription "${AZURE_SUBSCRIPTION_ID}" \
  --output json
```

It requires PostgreSQL 16 and `Standard_B1ms` in the selected region for the
subscription. It does not substitute another server SKU or region. If either is
absent:

1. Confirm `Microsoft.DBforPostgreSQL` is registered.
2. Check the error for subscription restrictions or regional capability
   reasons.
3. Use a permitted subscription or choose another workshop region only after
   verifying that Azure SRE Agent, the VM SKU, and PostgreSQL are all available
   there.
4. Create a fresh environment for a region change.

Do not edit deployment state to pretend the preflight succeeded.

### The selected VM size is unavailable

The VM preflight checks x64, Generation 2 support, regional restrictions, and
quota. Choose an available x64 size:

```bash
azd env set VM_SIZE "<available-x64-vm-size>"
azd up
```

Prefer at least 4 GiB RAM. Burstable application VM credit behavior can distort
the CPU exercise. The PostgreSQL `Standard_B1ms` tier is separate from the
application VM size.

### Policy blocks private PostgreSQL or the public API

The workshop requires:

* A public Standard IP and inbound TCP 8080 for the disposable Orders API.
* A delegated subnet for
  `Microsoft.DBforPostgreSQL/flexibleServers`.
* A private DNS zone and VNet link.
* PostgreSQL public access disabled.
* Managed identities and role assignments.
* Outbound access for package retrieval, Azure monitoring, and managed-identity
  token acquisition.
* Log Analytics, Application Insights, alert rules, and Azure SRE Agent.

No policy exemptions are created. Use an approved subscription or redesign the
lab through normal review. Do not weaken organizational policy from this
workshop.

### SRE Agent is unavailable in the region

The preview service has regional and subscription constraints. Use
`australiaeast` unless preflight confirms another region. Model availability
can also vary by subscription.

### `azd up` provisioned resources but failed later

List the saved evidence:

=== "Bash"

    ```bash
    find ".workshop/${AZURE_ENV_NAME}" -maxdepth 1 -type f -print
    ```

=== "PowerShell"

    ```powershell
    Get-ChildItem ".workshop/$env:AZURE_ENV_NAME"
    ```

Read the named deployment stage. Guest logs are available through Run Command:

=== "Bash"

    ```bash
    az vm run-command invoke \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --command-id RunShellScript \
      --scripts "tail -n 150 /var/log/orders-deployment.log; journalctl -u orders-api -n 100 --no-pager" \
      --output json
    ```

=== "PowerShell"

    ```powershell
    az vm run-command invoke `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --command-id RunShellScript `
      --scripts "tail -n 150 /var/log/orders-deployment.log; journalctl -u orders-api -n 100 --no-pager" `
      --output json
    ```

Correct the reported cause and rerun `azd up`. Schema and seed bootstrap are
idempotent, and the normal PostgreSQL rule is reconciled to `Allow`.

## Public API and service

### The Orders GUI or API is unreachable

Check the exact URL, VM state, and NSG:

=== "Bash"

    ```bash
    echo "${SERVICE_ORDERS_API_ENDPOINT_URL}"
    az vm get-instance-view \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --query "instanceView.statuses[].displayStatus" \
      --output table
    az network nsg rule list \
      --resource-group "${RESOURCE_GROUP}" \
      --nsg-name "${NETWORK_SECURITY_GROUP_NAME}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    $env:SERVICE_ORDERS_API_ENDPOINT_URL
    az vm get-instance-view `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --query "instanceView.statuses[].displayStatus" `
      --output table
    az network nsg rule list `
      --resource-group $env:RESOURCE_GROUP `
      --nsg-name $env:NETWORK_SECURITY_GROUP_NAME `
      --output table
    ```

Only inbound TCP 8080 is allowed. Port 22 is intentionally denied.

Run:

```bash
python scripts/workshop.py inspect
python scripts/workshop.py smoke
```

If the GUI shell loads but order or status cards fail, the public web process is
reachable and the underlying JSON route needs diagnosis. Check browser
developer tools and call `/health/live`, `/health/ready`, and `/database`
directly.

### The base URL returns JSON instead of HTML

The root uses content negotiation. `curl`, `Invoke-RestMethod`, and probes
normally receive the JSON descriptor. A browser address-bar navigation requests
HTML.

```bash
curl --silent --fail \
  --header 'Accept: text/html' \
  --output /dev/null \
  --write-out '%{http_code} %{content_type}\n' \
  "${SERVICE_ORDERS_API_ENDPOINT_URL}/"
```

Expect HTTP 200 and `text/html`. Rerun `azd up` if the checksummed bundle is
missing static assets.

### Liveness is 200 but readiness is 503

This is a meaningful distinction:

* `/health/live` checks the API process.
* `/health/ready` executes a PostgreSQL schema probe.

First inspect fault state:

```bash
python scripts/workshop.py fault status
```

If access is `Deny`, reset it. If access is `Allow`, continue with private DNS,
TCP 5432, identity, and schema sections below. Restarting the API does not fix an
NSG deny or broken private DNS.

### `/database` or order operations return HTTP 503

The application maps PostgreSQL connectivity, timeout, and database errors to
HTTP 503 and records failed dependencies and exceptions.

```bash
curl --silent "${SERVICE_ORDERS_API_ENDPOINT_URL}/database" | jq .
python scripts/workshop.py fault status
python scripts/workshop.py inspect
```

Use Application Insights **Failures** and query:

```kusto
union AppDependencies, AppExceptions
| where TimeGenerated > ago(30m)
| where AppRoleName == "orders-api"
| where DependencyType == "PostgreSQL" or Type == "AppExceptions"
| project TimeGenerated, Type, DependencyType, Name, Success, ResultCode,
    ProblemId, OuterMessage
| order by TimeGenerated desc
```

Do not drop or recreate the `orders` database as a generic recovery step.

### `/fault/*` returns 404

That is expected. Use authenticated controls:

```bash
python scripts/workshop.py fault cpu 600 2
python scripts/workshop.py fault postgresql
python scripts/workshop.py fault status
python scripts/workshop.py fault reset-cpu
python scripts/workshop.py fault reset-postgresql
```

## Private DNS and TCP 5432

### PostgreSQL private DNS does not resolve

Confirm the configured host and zone:

=== "Bash"

    ```bash
    echo "${POSTGRESQL_HOST}"
    az network private-dns zone show \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${POSTGRESQL_PRIVATE_DNS_ZONE_NAME}" \
      --output table
    az network private-dns link vnet list \
      --resource-group "${RESOURCE_GROUP}" \
      --zone-name "${POSTGRESQL_PRIVATE_DNS_ZONE_NAME}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    $env:POSTGRESQL_HOST
    az network private-dns zone show `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:POSTGRESQL_PRIVATE_DNS_ZONE_NAME `
      --output table
    az network private-dns link vnet list `
      --resource-group $env:RESOURCE_GROUP `
      --zone-name $env:POSTGRESQL_PRIVATE_DNS_ZONE_NAME `
      --output table
    ```

Resolve from the VM, not from your laptop:

=== "Bash"

    ```bash
    az vm run-command invoke \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --command-id RunShellScript \
      --scripts "getent ahostsv4 '${POSTGRESQL_HOST}'" \
      --output json
    ```

=== "PowerShell"

    ```powershell
    az vm run-command invoke `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --command-id RunShellScript `
      --scripts "getent ahostsv4 '$env:POSTGRESQL_HOST'" `
      --output json
    ```

The result must contain only private addresses. Confirm the VNet link targets
the workshop VNet and the server references the expected private zone. Rerun
`azd up` to reconcile deployment-managed resources. Do not add a public DNS or
hosts-file workaround.

### DNS resolves but TCP 5432 fails

Make sure the workshop fault is not active:

```bash
python scripts/workshop.py fault status
python scripts/workshop.py fault reset-postgresql
```

Verify the exact rule:

```bash
az network nsg rule show \
  --resource-group "${RESOURCE_GROUP}" \
  --nsg-name "${NETWORK_SECURITY_GROUP_NAME}" \
  --name "${POSTGRESQL_FAULT_RULE_NAME}" \
  --query "{Access:access,Priority:priority,Direction:direction,Protocol:protocol,Source:sourceAddressPrefix,Destination:destinationAddressPrefix,Port:destinationPortRange}" \
  --output table
```

Healthy values are `Allow`, priority 100, outbound TCP, source
`10.240.0.0/27`, destination `10.240.0.32/27`, and port 5432.

Probe from the VM after reset:

=== "Bash"

    ```bash
    az vm run-command invoke \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --command-id RunShellScript \
      --scripts "timeout 5 bash -c 'exec 3<>/dev/tcp/${POSTGRESQL_HOST}/5432'; echo tcp_exit=\$?" \
      --output json
    ```

=== "PowerShell"

    ```powershell
    $script = "timeout 5 bash -c 'exec 3<>/dev/tcp/$env:POSTGRESQL_HOST/5432'; " +
      'echo tcp_exit=$?'
    az vm run-command invoke `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --command-id RunShellScript `
      --scripts $script `
      --output json
    ```

Healthy access returns exit code 0. If rule shape drift is reported, use
`azd up` to restore the deployment-owned definition.

## Managed identity and TLS

### The VM cannot obtain or use a PostgreSQL token

`python scripts/workshop.py inspect` obtains a token from the VM metadata
endpoint for:

```text
https://ossrdbms-aad.database.windows.net
```

It uses the token with `psql` without printing or persisting it. Do not run a
debug command that writes the token to terminal output.

Compare the VM identity and PostgreSQL Entra administrator:

=== "Bash"

    ```bash
    az vm show \
      --resource-group "${RESOURCE_GROUP}" \
      --name "${VM_NAME}" \
      --query "{Vm:name,PrincipalId:identity.principalId,IdentityType:identity.type}" \
      --output table

    az postgres flexible-server ad-admin list \
      --resource-group "${RESOURCE_GROUP}" \
      --server-name "${POSTGRESQL_SERVER_NAME}" \
      --query "[].{Name:principalName,ObjectId:objectId,Type:principalType,Tenant:tenantId}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    az vm show `
      --resource-group $env:RESOURCE_GROUP `
      --name $env:VM_NAME `
      --query "{Vm:name,PrincipalId:identity.principalId,IdentityType:identity.type}" `
      --output table

    az postgres flexible-server ad-admin list `
      --resource-group $env:RESOURCE_GROUP `
      --server-name $env:POSTGRESQL_SERVER_NAME `
      --query "[].{Name:principalName,ObjectId:objectId,Type:principalType,Tenant:tenantId}" `
      --output table
    ```

The administrator object ID must match the VM principal ID and its principal
name should match `POSTGRESQL_USER`. Entra administrator propagation can take
time after initial provisioning. The guest installer retries bootstrap.

The combined administrator/runtime identity is intentional only for this
disposable workshop. Do not fix an authentication failure by enabling password
authentication or adding a password to `/etc/orders-api.env`.

### TLS or hostname verification fails

Inspection requires:

```text
SSL Mode=VerifyFull
```

The host must be the full `*.postgres.database.azure.com` name so certificate
hostname validation succeeds. Do not replace it with the resolved private IP
and do not downgrade TLS verification.

Rerun `azd up` to rewrite the deployment-managed application configuration. If
certificate validation still fails, confirm system time and the installed CA
certificate package through Run Command.

## Schema bootstrap

### Bootstrap reports missing schema or seed rows

The bootstrap:

* Takes a PostgreSQL advisory transaction lock.
* Applies migration version 1 transactionally.
* Creates `orders` and `order_requests`.
* Inserts five deterministic negative-ID seed rows with conflict-safe writes.
* Synchronizes the positive identity sequence.

Run:

```bash
python scripts/workshop.py inspect
```

Then inspect the guest deployment and service logs. Common causes are:

* Private DNS or TCP 5432 not ready.
* The PostgreSQL fault rule left at `Deny`.
* Entra administrator propagation incomplete.
* Token acquisition or TLS failure.
* A partially modified schema outside the supported deployment path.

After correcting infrastructure or propagation, rerun:

```bash
azd up
```

Do not manually mark a migration complete, delete seed rows, or recreate the
server. Preserve the reported state for diagnosis.

### Readiness says the schema is unavailable

Readiness requires migration version 1 and both required tables. A running
server alone is insufficient.

Query recent telemetry:

```kusto
AppDependencies
| where TimeGenerated > ago(30m)
| where AppRoleName == "orders-api"
| where DependencyType == "PostgreSQL"
| project TimeGenerated, Name, Success, ResultCode, Data
| order by TimeGenerated desc
```

Use `azd up` for the supported schema bootstrap after connectivity and identity
are healthy.

## Fault controls

### VM Run Command times out

A timeout does not prove that the guest CPU action failed. Run:

```bash
python scripts/workshop.py fault status
```

If CPU is active, continue the exercise or run `fault reset-cpu`. Do not submit
another CPU fault blindly.

### The PostgreSQL rule is stuck at `Deny`

Run the idempotent reset and read-back:

```bash
python scripts/workshop.py fault reset-postgresql
python scripts/workshop.py fault status
```

Expected PostgreSQL state is `inactive` and access `Allow`. Confirm the child
rule directly with `az network nsg rule show`.

If reset failed:

1. Read `.workshop/<environment>/fault-reset-postgresql.json` or
   `fault-reset-postgresql-partial.json` when present.
2. Check Azure CLI authentication and write permission on the rule.
3. Check for an in-progress `orders-postgresql-fault` resource-group
   deployment.
4. Rerun reset after the operation completes.
5. Run `azd up` if the rule shape drifted; deployment reconciles all fixed
   properties and `Allow`.

Do not delete the rule. Its explicit normal `Allow` state and fixed scope are
part of the safety model.

### PostgreSQL access is denied but requests still succeed

Existing Npgsql pooled connections and Azure's stateful flow handling can delay
the first failed new connection. Confirm the rule is `Deny`, keep the Module 04
sample calls running, and record the first failure.

Do not broaden the deny, deny all outbound traffic, restart PostgreSQL, or alter
data to force an immediate failure. If no dependency fails after the documented
observation window, preserve the evidence and report the environment behavior.

### PostgreSQL fault or reset reports unexpected rule properties

The helper refuses to operate unless every bounded property matches:

* Priority 100.
* Outbound TCP.
* Source `10.240.0.0/27`.
* Destination `10.240.0.32/27`.
* Destination port 5432.

Run `azd up` to reconcile the deployment-owned rule. Do not modify the helper to
accept a broader scope.

### Browser rule update is interrupted

The browser performs GET, conditional PUT with `If-Match`, then GET read-back.
An interrupted response can be ambiguous. Select **Check fault status** before
retrying. Another writer can produce an ETag conflict, which protects against
silently overwriting a concurrent change.

## Telemetry and alerts

### `python scripts/workshop.py telemetry` times out

The command waits up to ten minutes for heartbeat, guest CPU, API requests,
PostgreSQL dependencies, and availability results. It reports missing signal
names.

Check Azure Monitor Agent and the Data Collection Rule association:

=== "Bash"

    ```bash
    az vm extension list \
      --resource-group "${RESOURCE_GROUP}" \
      --vm-name "${VM_NAME}" \
      --query "[].{Name:name,State:provisioningState}" \
      --output table

    az rest \
      --method get \
      --uri "https://management.azure.com${VM_RESOURCE_ID}/providers/Microsoft.Insights/dataCollectionRuleAssociations?api-version=2023-03-11" \
      --query "value[].{Name:name,Rule:properties.dataCollectionRuleId}" \
      --output table
    ```

=== "PowerShell"

    ```powershell
    az vm extension list `
      --resource-group $env:RESOURCE_GROUP `
      --vm-name $env:VM_NAME `
      --query "[].{Name:name,State:provisioningState}" `
      --output table

    az rest `
      --method get `
      --uri "https://management.azure.com$($env:VM_RESOURCE_ID)/providers/Microsoft.Insights/dataCollectionRuleAssociations?api-version=2023-03-11" `
      --query "value[].{Name:name,Rule:properties.dataCollectionRuleId}" `
      --output table
    ```

Generate deterministic application samples:

```bash
curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/orders" > /dev/null
curl --silent --fail "${SERVICE_ORDERS_API_ENDPOINT_URL}/database" > /dev/null
```

Wait several minutes and retry. Application Insights, workspace tables, and
alert evaluation are asynchronous. Do not treat a portal delay as proof that
the application emitted no telemetry.

### PostgreSQL dependencies do not appear

Query the exact type:

```kusto
AppDependencies
| where TimeGenerated > ago(30m)
| where AppRoleName == "orders-api"
| summarize Samples=sum(ItemCount), Failures=sumif(ItemCount, Success == false)
  by DependencyType, Name
| order by Samples desc
```

The required type is `PostgreSQL`. Call `/orders`, `/health/ready`, and
`/database`, wait for ingestion, and verify that you selected this workshop's
`appi-<suffix>` and workspace.

### The PostgreSQL connectivity alert does not fire

The rule `alert-orders-postgresql-connectivity` requires at least one failed
PostgreSQL dependency in the last five minutes. Query its source:

```kusto
AppDependencies
| where TimeGenerated > ago(30m)
| where AppRoleName == "orders-api"
| where DependencyType == "PostgreSQL" and Success == false
| project TimeGenerated, Name, ResultCode, ItemCount
| order by TimeGenerated desc
```

If this query is empty, the alert has no qualifying evidence. Confirm the NSG
rule is `Deny` and follow Module 04 without broadening the fault. If failures are
present, allow for ingestion and the one-minute evaluation cycle, then inspect
the alert rule state.

### The CPU alert does not fire

Confirm the VM **Percentage CPU** average stayed above 80 percent across the
five-minute window. A shorter burst should not fire. Use the documented
ten-minute, two-worker fault, adjusted only within the helper's validated worker
range when the selected VM size requires it.

### An HTTP 5xx alert also fires during Module 04

This can be expected when more than ten database-backed requests return 5xx in
five minutes. The HTTP alert demonstrates customer impact; the PostgreSQL
dependency alert identifies the failing boundary. Verify timing before treating
them as one incident.

## Azure SRE Agent

### The response plan is missing

Read:

=== "Bash"

    ```bash
    cat ".workshop/${AZURE_ENV_NAME}/sre-agent-configuration.json"
    ```

=== "PowerShell"

    ```powershell
    Get-Content ".workshop/$env:AZURE_ENV_NAME/sre-agent-configuration.json"
    ```

Rerun `azd up` to reconcile `workshop-sev1-sev2-review`. Do not create a second
overlapping catch-all plan.

If the optional incident workflow is installed, run
`python scripts/workshop.py incident-workflow status` first. Provisioning
preserves a healthy two-plan title partition and refuses to overwrite one
with missing rollback state or drift. Reconcile or remove that module before
restoring a catch-all.

### The optional incident workflow does not route as expected

The agent must manage only the selected workshop resource group so its global
title partition cannot divert another environment's identically named alerts.

Run:

```bash
python scripts/workshop.py incident-workflow status
```

Compare `routing.workflowTitleContains` (`postgresql`) and
`routing.originalTitleContains` (`alert-orders-h`) with the actual incident
title. Both plans must be enabled for Sev1/Sev2 in Review mode. The latter
prefix covers CPU and HTTP 5xx; PostgreSQL uses the dedicated investigator.
Another enabled plan that matches the same titles causes installation to fail.
Live Stable-preview verification retained `titleContainsAny`,
`titleNotContains`, and target-resource guards but still misrouted CPU, so do
not substitute those stored fields for the scalar predicates.

Read the new alert instance's matched plan and actual handling agent in its
thread. A related-alert merge can reuse an earlier thread without proving that
the new investigator ran. If the preview drops filter fields or dispatches
incorrectly, reset the fault and restore the original routing with
`incident-workflow remove` before further exercises.

### Incident workflow reports drift or a partial operation

Keep `.workshop/<environment>/incident-workflow-state.json`. It is the
environment/agent-bound snapshot needed to restore the original filters.
Installation and removal compare live configuration before writes; a supplied
strong ETag is used conditionally. With no ETag these preview checks are
best-effort, not atomic; weak or malformed ETags are rejected. Avoid concurrent
portal edits.

Resolve the named conflict with its owner before removing/reinstalling. Do not
delete the state file or enable the new plan beside an unrestricted original
plan. A lost activation response is not blindly retried; rollback first disables
the new plan and restores the original.

A stale `incident-workflow-state.lock` can remain after a terminated process.
Read its process ID and confirm the process is no longer operating before
removing that specific lock file. Inspect partial state before retrying.

### An optional GitHub or Outlook follow-up is disabled

Inspect each `followups` entry and its reason. The exact incident repository
must be configured through Code Access with authenticated GitHub access.
The selected Outlook MCP server must bind one Office 365 connection whose
`overallStatus` is `Connected`, with explicitly selected To recipients.

The corresponding native write tool needs an exact Ask policy, with no
matching Allow or Deny override. The installer does not create connections,
complete OAuth, or replace global policies. Missing prerequisites leave the
write tool unattached and the skill draft-only. Tool availability and an actual
per-write approval interaction still need confirmation in your thread.

Remove and reinstall after changing connections, capabilities, definitions,
or trusted destinations. Do not switch repositories/mailboxes inside an
incident. For an unknown issue/send outcome, reconcile the correlation marker
and receipts rather than repeating the write.

### An alert fired but no investigation appears

Confirm:

* Alert severity is Sev1 or Sev2.
* The response plan is enabled for Azure Monitor.
* The alert belongs to this workshop resource group.
* The response-plan filter does not target another resource.

Allow for service propagation. Continue investigating with raw telemetry and
Activity Log while routing catches up.

### The agent can read resources but not telemetry

Review both SRE Agent identities. They need resource read and Log Analytics read
access. Role propagation can take several minutes. They do not need VM Run
Command or NSG write access.

### The agent proposes but does not execute reset

That is expected. The workshop uses Review mode and read-only runtime roles. A
human uses a separate authenticated identity:

```bash
python scripts/workshop.py fault reset-cpu        # CPU incident
python scripts/workshop.py fault reset-postgresql # PostgreSQL incident
```

Do not grant workload write access to make the demonstration automatic.

### `health-check install` returns 403 or 404

Right after provisioning, the SRE Agent data plane can reject calls until role
assignments propagate. The command retries these responses for about two
minutes. If it still fails, confirm that you hold the agent-scoped administrator
role and run the command again. It is safe to repeat.

### `health-check install` says the log-analytics connector is not healthy

The health check reads the `App*`, `Heartbeat`, and `Perf` tables through the
`log-analytics` connector. Open the agent's Connectors page in the portal, wait
for the connector to report a healthy state, and run the command again. Rerun
`azd up` only if the connector is missing.

### `health-check status` reports `matchesDefinition` as `false`

Someone edited the task, custom agent, or skill in the portal. Read the
`problems` list, then run `health-check install`. It rewrites all three items,
including the tool list, from the definitions under `agent/`.

### The scheduled task shows Failed or never runs

The portal marks a task `Failed` after three consecutive failed runs. Open the
task, read the latest run thread for the error, and run `health-check install` to
rewrite it. The schedule is evaluated in UTC and runs on weekdays only:
`0 21 * * 0-4` is Monday to Friday at 09:00 NZST, which is 10:00 during New
Zealand daylight time. Use **Run task now** to test without waiting.

### The health-check report says the Application Insights cross-check was unavailable

This is expected in the current preview. Scheduled runs did not expose
`QueryAppInsightsUsingAppId` in testing, even when the `app-insights` connector
was healthy. The agent reads the same Application Insights data from the `App*`
tables of the Log Analytics workspace, so treat the message as a gap only when
those tables are also empty or the report has no request and dependency numbers.

### The health-check report says Unknown or has no charts

An area with no samples in the last 15 minutes is reported as Unknown by design.
Start traffic with `./scripts/generate-load.sh`, wait for telemetry ingestion, and
run the task again. Charts are omitted when data is empty or too sparse to plot.
Do not read a missing chart as a healthy signal.

### The Live Report cannot read a table

A Live Report can use only the connectors already configured for the agent. Ask
the agent in the authoring thread to list the data sources it can use, then
adjust the report. Confirm that you have read and write permission on the agent
and that the `log-analytics` and `app-insights` connectors are healthy. Live
Reports is a preview feature, so record any limitation in your notes.

### Live Reports is missing, or you cannot create, approve, or delete a report

Creating a report, and running its queries when you open or reload it, needs the
SRE Agent Standard User, SRE Agent Author, or SRE Agent Administrator role on the
agent. Approving the report's tool call, and deleting a report, needs SRE Agent
Administrator. `azd up` assigns the Administrator role to the person who
deployed the agent. For a shared agent, ask its owner to assign the role at the
agent scope, wait a few minutes, then sign out of the portal and back in.

### The agent says its telemetry queries returned 403 InsufficientAccessError

Role assignments take several minutes to propagate after `azd up`. The
deployment already gives both agent identities the Log Analytics Reader role on
the workspace, so do not add more assignments. The agent may suggest an
`az role assignment create` command that uses the identity's client ID. Check
the existing assignments first with
`az role assignment list --scope <workspace resource ID>`. Wait a few minutes,
then tell the authoring thread: "Retry the telemetry probes and continue
building the report." In testing, the same request succeeded about seven minutes
after the deployment finished.

### A Live Report status looks wrong

The agent generates the report's code, so a saved report can have defects. In
testing, one version showed PostgreSQL as No data although its table listed more
than 1,200 calls, and an earlier wording marked healthy areas Degraded because a
recovered outage was inside the 24-hour window. Compare each status with its
table and your Task 5 queries. Then select **Open authoring thread** and describe
the defect, for example: "The PostgreSQL status shows No data but the table shows
calls. Fix the status code for every area, test it against fresh query results,
and save a new version."

### There are two Live Reports with the same name

Every request from **+ New report** creates a new report, even when the name
matches. Delete the extra report from its overflow menu, which needs SRE Agent
Administrator. To change an existing report, open its authoring thread instead.

## Documentation-site Azure connection

### Environment discovery returns no resource group

The browser discovers only groups whose name uses the workshop prefix and whose
architecture tag is `single-vm-postgresql-v1`. Confirm the participant can read
the subscription, group, VM, workspace, NSG, PostgreSQL server, and optional SRE
Agent resource.

### CPU works but PostgreSQL control returns 403

VM Run Command permission does not grant network rule write. The participant
also needs read and write actions for the exact
`POSTGRESQL_FAULT_RULE_RESOURCE_ID`. The deployment does not create this
assignment.

Prefer a custom role assigned at the narrowest supported scope. Do not use a
broad subscription-level Network Contributor assignment as a shortcut.

### The graph is empty but controls work

The CPU graph uses Azure Monitor Metrics. The PostgreSQL graph uses Log
Analytics `Data.Read`. Confirm the SPA has the delegated Log Analytics API
permission, tenant admin consent is complete, the participant can query the
workspace, and dependency samples have arrived.

## Cleanup

### `azd down --purge --force` stalls

Check state and locks:

=== "Bash"

    ```bash
    az group show \
      --name "${RESOURCE_GROUP}" \
      --query properties.provisioningState
    az lock list --resource-group "${RESOURCE_GROUP}" --output table
    ```

=== "PowerShell"

    ```powershell
    az group show `
      --name $env:RESOURCE_GROUP `
      --query properties.provisioningState
    az lock list --resource-group $env:RESOURCE_GROUP --output table
    ```

Private DNS links, delegated subnets, and PostgreSQL can make deletion ordering
visible while Azure completes the resource-group operation. Review Activity Log
and policy failures. Delete only the confirmed workshop scope.

Reset before another cleanup attempt:

```bash
python scripts/workshop.py fault reset
azd down --purge --force
```

Reset does not stop billing. Only complete resource deletion removes the VM,
PostgreSQL, private DNS and network resources, monitoring, alerts, and SRE
Agent.

<div class="sre-nav" markdown>
[:material-arrow-left: Workshop Variables](01-variables.md)
[Cost Management :material-arrow-right:](03-cost-management.md)
</div>
