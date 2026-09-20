---
title: Troubleshooting
description: Diagnosis and resolution for the failures most commonly encountered while running the Azure SRE Agent workshop.
ms.date: 2026-09-08
ms.topic: troubleshooting
keywords:
  - troubleshooting
  - errors
  - azure cli
estimated_reading_time: 10
---

## How to use this page

Find the symptom, confirm the diagnosis with the check command, then apply the resolution. If your problem is not here, the module-specific validation sections usually narrow it down faster than guessing.

## Deployment problems

### A fresh deployment cannot reuse a deleted Key Vault name

The vault has purge protection and seven-day soft-delete retention. Cleanup
cannot immediately purge it, even with `azd down --purge`. Reusing the same
environment-derived vault name during retention may require recovery. Use a new
azd environment name for a separate fresh workshop, wait for retention to expire,
or follow your organization's approved recovery process; do not bypass protection.

### Deployment fails with MissingSubscriptionRegistration

The pre-provision hook automatically registers required resource providers and
waits up to fifteen minutes. This error indicates registration failed, timed out,
or is blocked for the deployment caller. The hook prints registration requests
and pending provider names and states while it waits; a timeout also lists the
providers still pending.

In a second terminal, use `az provider show` to inspect a provider reported by
the hook without interrupting deployment. Ensure Azure CLI is using the same
subscription as `azd`. For example:

```bash
az provider show --namespace Microsoft.Insights \
  --query "{Namespace:namespace, State:registrationState}" \
  --output table
```

Older hooks compared provider namespaces case-sensitively. Azure can return
`microsoft.insights` for `Microsoft.Insights`, causing a false timeout even when
it is registered. If the hook stays silent and times out despite registered
providers, update `scripts/workshop.py` in the checkout where you run `azd up`
before retrying. Updating a separate worktree does not update your deployment
checkout.

Check the hook output and the caller's subscription permissions, including
permission to register providers. Resolve access or policy restrictions, then
rerun `azd up` so the hook can retry. `Microsoft.ContainerInstance` and
`Microsoft.Storage` must also be available for the deployment script.

### Container registry name is already taken

Registry names are globally unique. Your suffix collided with someone else's.

```bash
az acr check-name --name "acr${WORKSHOP_SUFFIX}" --output table
```

Choose a new suffix and start Module 03 again. The partially created resource group can be deleted first.

### SQL initialization job fails

SQL is Entra-only, so password resets are not a troubleshooting step. Inspect the
failed manual-trigger job execution and its logs. Confirm the bootstrap job uses
its separate administrator managed identity, the Entra administrator configuration
has propagated, and SQL networking allows the job to connect. Rerun `azd up`
after addressing the reported failure. The hook waits for job success before
agent configuration; existing orders and ballast are not deleted on retry.

```bash
source .workshop/workshop.env
az containerapp job execution list --name "${BOOTSTRAP_JOB_NAME}" \
  --resource-group "${RESOURCE_GROUP}" --output table
```

### A hook cannot import yaml or authenticate

Activate the same Python 3.10-or-later environment used to install
`python -m pip install -r requirements.txt`. Check `python -c "import yaml"`.
Run both `az login` and `azd auth login` in the intended tenant, and ensure both
CLIs select the same subscription. `azd` authentication alone does not authenticate
the Azure CLI commands used by the hooks.

The hook rejects differing `oid` or `tid` claims in the CLIs' ARM tokens. Sign in
to both with the same attendee identity and tenant; do not override deployment
identity variables to bypass the check. azd supplies `AZURE_PRINCIPAL_ID` and
`AZURE_PRINCIPAL_TYPE` before pre-provision runs.

### Deployment fails while creating role assignments

The deployment caller needs subscription `Owner`, or `Contributor` plus
`User Access Administrator`, including resource group creation and all role
assignments. Resource-group-only access or Contributor alone is insufficient.
Resolve the prerequisite access with your subscription administrator and rerun
`azd up`; do not manually grant missing runtime roles.

### Deployment succeeds but the container app shows ImagePullFailure

Role assignment propagation for `AcrPull` has not completed.

```bash
az containerapp revision list \
  --name orders-api \
  --resource-group "${RESOURCE_GROUP}" \
  --query "[].{Revision:name, Active:properties.active, Health:properties.healthState, Running:properties.runningState}" \
  --output table
```

Wait three to five minutes, then restart the revision.

```bash
REVISION=$(az containerapp revision list --name orders-api --resource-group "${RESOURCE_GROUP}" --query "[0].name" --output tsv)
az containerapp revision restart --name orders-api --resource-group "${RESOURCE_GROUP}" --revision "${REVISION}"
```

If it persists, verify the role assignment exists.

```bash
IDENTITY_ID=$(az identity show --name "id-${WORKSHOP_SUFFIX}" --resource-group "${RESOURCE_GROUP}" --query principalId --output tsv)
az role assignment list --assignee "${IDENTITY_ID}" --all --query "[].{Role:roleDefinitionName, Scope:scope}" --output table
```

## Application problems

### POST /orders returns 500 with a catalog message

`catalog-api` is not reachable or not ready.

```bash
az containerapp show --name catalog-api --resource-group "${RESOURCE_GROUP}" \
  --query "{Status:properties.runningStatus, Ingress:properties.configuration.ingress}" --output json

az containerapp logs show --name catalog-api --resource-group "${RESOURCE_GROUP}" --tail 50
```

Confirm `Catalog__BaseUrl` on `orders-api` is `http://catalog-api`. Internal service discovery uses the app name, not a fully qualified hostname.

```bash
az containerapp show --name orders-api --resource-group "${RESOURCE_GROUP}" \
  --query "properties.template.containers[0].env[?name=='Catalog__BaseUrl']" --output table
```

### POST /orders returns 500 with a SQL message

Either the firewall rule is missing or the connection string is wrong.

```bash
az sql server firewall-rule list --server "${SQL_SERVER_NAME}" --resource-group "${RESOURCE_GROUP}" --output table
```

`AllowAllWindowsAzureIps` with a start and end address of `0.0.0.0` must exist. Recreate it if missing.

```bash
az sql server firewall-rule create \
  --server "${SQL_SERVER_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --name AllowAllWindowsAzureIps \
  --start-ip-address 0.0.0.0 \
  --end-ip-address 0.0.0.0
```

If the firewall is correct, inspect the SQL bootstrap job result and the Orders
managed identity configuration. Initialization must create the contained user and
object-level grants before writes succeed. Do not grant `db_owner` to the API or
add SQL authentication; reconcile the deployment with `azd up`.

### Fault endpoints return 404

Fault injection is disabled or the token is not configured.

```bash
az containerapp show --name orders-api --resource-group "${RESOURCE_GROUP}" \
  --query "properties.template.containers[0].env[?name=='Fault__Enabled' || name=='Fault__Token']" --output table
```

`Fault__Enabled` must be `true` and `Fault__Token` must reference the Key Vault-backed
Container Apps secret. Rerun `azd up` if either is wrong.

### Fault endpoints return 401

Use `./scripts/inject-fault.sh status` or `./scripts/inject-fault.ps1 status`,
which retrieves the current secret from Key Vault just in time. Verify the
selected environment, Azure CLI login, and deployment-assigned Key Vault access.
Allow time for role propagation and secret-reference refresh, then rerun
`azd up` if configuration has drifted. Do not print or persist the token.

## Telemetry problems

### No rows in AppRequests

Application Insights is not receiving telemetry, or you are querying too soon.

```bash
az containerapp show --name orders-api --resource-group "${RESOURCE_GROUP}" \
  --query "properties.template.containers[0].env[?name=='APPLICATIONINSIGHTS_CONNECTION_STRING']" --output table
```

Ingestion takes two to five minutes. If the connection string is present and traffic is flowing, wait and retry before changing anything.

### az monitor log-analytics query fails with a workspace error

The command needs the workspace GUID, not the resource name or resource ID.

```bash
az monitor log-analytics workspace show \
  --name "${LOG_ANALYTICS_NAME}" \
  --resource-group "${RESOURCE_GROUP}" \
  --query customerId --output tsv
```

Use that value for `LOG_ANALYTICS_CUSTOMER_ID`.

### No rows in ContainerAppConsoleLogs_CL

Container Apps log ingestion has its own delay and its own configuration.

```bash
az containerapp env show --name "${CONTAINER_ENV_NAME}" --resource-group "${RESOURCE_GROUP}" \
  --query "properties.appLogsConfiguration" --output json
```

`destination` must be `log-analytics`. If it is null, the environment was created without log forwarding and needs to be redeployed.

### Alerts never fire

Three causes account for nearly all cases.

```bash
az monitor metrics alert list --resource-group "${RESOURCE_GROUP}" \
  --query "[].{Name:name, Enabled:enabled, Window:windowSize, Frequency:evaluationFrequency}" --output table
```

* The condition genuinely never crossed the threshold. Check the metric directly with `az monitor metrics list`.
* Not enough time has elapsed. A 5-minute window needs a full window of qualifying data plus evaluation time, so 6 to 8 minutes is normal.
* The metric has no data because the dimension filter matches nothing. The 5xx alert filters on `statusCodeCategory`, which produces no data points at all when there are no 5xx responses.

### Alert emails never arrive

```bash
az monitor action-group show --name ag-sre-workshop --resource-group "${RESOURCE_GROUP}" \
  --query "emailReceivers[].{Email:emailAddress, Status:status}" --output table
```

The receiver is optional. If `ALERT_EMAIL` was not set in the azd environment,
an empty receiver list is expected. To enable email, set
`azd env set ALERT_EMAIL "you@example.com"` and rerun `azd up`. Hooks do not query
Microsoft Graph for an email address. For a configured receiver, inspect its
status and check spam for delivery or confirmation instructions.

## Azure SRE Agent problems

### The agent reports it cannot see any resources

Role assignments have not propagated, or they were never created.

```bash
az role assignment list --assignee "${SRE_AGENT_PRINCIPAL_ID}" --all \
  --query "[].{Role:roleDefinitionName, Scope:scope}" --output table
```

Expect `Reader` and `Monitoring Reader` on the resource group and `Log Analytics Reader` on the workspace. Propagation takes up to five minutes.

### The agent sees resources but cannot query telemetry

`Log Analytics Reader` is missing or is scoped to the wrong resource.

Check the role assignment list against the [permission record](../05-configure-sre-agent/index.md#deployment-api-contract-and-permission-record).
Wait for propagation, then rerun `azd up` to reconcile assignments. Do not add
manual grants or broaden the runtime's read-only scope.

### Agent configuration returns 401 or 403

The configuration caller, not the runtime identity, needs the agent-scoped
`SRE Agent Administrator` role assigned by hooks. Check the Azure CLI account and
allow time for that grant to propagate. The configuration API uses the resource's
`properties.agentEndpoint` and token audience `https://azuresre.dev`.
Retry `python scripts/workshop.py configure-agent` after correcting authentication.

### Agent synchronization reports a retired incident filter

Keep the named `workshop-` filter in `agent/incident-filters.yaml` with
`spec.isEnabled: false`. Removing an existing filter from YAML fails closed;
the hook does not assume a v2 DELETE endpoint exists.

### Knowledge indexing does not finish

The hook replaces owned `workshop-*.md` documents and polls `/files` for
`isIndexed` or `indexStatus` for up to ten minutes. Confirm the manifests reference
existing files relative to `agent/`, then retry the configuration refresh after
investigating a service-side indexing failure. Do not store unrelated documents
under the reserved `workshop-*.md` namespace. Index status does not prove remote
source-byte equality; the contract does not expose downloads of uploaded source.

### An investigation does not acknowledge or close the alert

That is expected. Full Azure Monitor alert lifecycle integration requires
subscription `Monitoring Contributor`, which this read-only workshop does not
grant. Use the Azure Monitor investigative UI to inspect alert state; do not widen
agent permissions to make a lifecycle action succeed.

### Azure SRE Agent is not available in my region

The deployment targets `Microsoft.App/agents@2025-05-01-preview`, whose availability
is constrained. Use the supported default `eastus2` and check the
[Azure SRE Agent documentation](https://learn.microsoft.com/azure/sre-agent/)
before selecting another region.

The pre-provision hook checks `AZURE_LOCATION` against the locations advertised
by `Microsoft.App/agents`. If that check fails, select a supported region in the
azd environment rather than creating a separate agent manually.

You can complete Modules 06 through 11 manually. Every investigation module includes the full KQL and Azure CLI commands, and the analytical content stands on its own.

## Cost problems

### The environment is costing more than expected

```bash
az resource list --resource-group "${RESOURCE_GROUP}" --query "[].{Name:name, Type:type}" --output table
```

The usual causes are a Container App left scaled out from a Module 07 experiment, a SQL database resized during Module 10, or Log Analytics ingestion from a load generator left running overnight. See [Cost Management](03-cost-management.md).

<div class="sre-nav" markdown>
[:material-arrow-left: Workshop Variables](01-variables.md)
[Cost Management :material-arrow-right:](03-cost-management.md)
</div>
