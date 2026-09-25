export const ARM_ENDPOINT = "https://management.azure.com";
export const LOG_ANALYTICS_ENDPOINT = "https://api.loganalytics.azure.com";
export const ARM_SCOPE = "https://management.azure.com/user_impersonation";
export const LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/Data.Read";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const REQUEST_ID = /^[0-9a-f]{32}$/;
const RESOURCE_ID =
  /^\/subscriptions\/[0-9a-f-]+\/resourceGroups\/[a-z0-9._()-]+\/providers\/[a-z0-9.]+\/[a-z0-9.]+\/[a-z0-9._()-]+$/i;

export const SCENARIOS = Object.freeze({
  cpu: Object.freeze({
    id: "cpu",
    action: "cpu",
    arguments: Object.freeze([600, 2]),
    runLabel: "Run CPU incident",
    chartTitle: "VM Percentage CPU",
    unitLabel: "CPU",
    threshold: 80,
    thresholdLabel: "Alert threshold: 80%",
  }),
  disk: Object.freeze({
    id: "disk",
    action: "disk",
    arguments: Object.freeze([90, 600]),
    runLabel: "Run disk incident",
    chartTitle: "/var/lib/orders free space",
    unitLabel: "free space",
    threshold: 15,
    thresholdLabel: "Alert threshold: 15% free",
  }),
});

export class WorkshopError extends Error {
  constructor(message, options = {}) {
    super(message, options);
    this.name = "WorkshopError";
  }
}

export function validateConfig(raw) {
  const defaults = {
    configured: false,
    tenantId: "",
    clientId: "",
    redirectUri: "",
    resourceGroupPrefix: String(raw?.resourceGroupPrefix || "rg-sre-agent-workshop-"),
    environmentTagName: String(raw?.environmentTagName || "workshop-architecture"),
    environmentTagValue: String(raw?.environmentTagValue || "single-vm"),
    refreshSeconds: Number(raw?.refreshSeconds) || 60,
  };
  const invalid = (error, cause) => ({ ...defaults, error, cause });
  const tenantId = String(raw?.tenantId || "").trim();
  const clientId = String(raw?.clientId || "").trim();
  const redirectUri = String(raw?.redirectUri || "").trim();

  if (!tenantId && !clientId) {
    return invalid(
        "Azure connection is not configured for this site. The workshop owner must set " +
          "the tenant and SPA client IDs.",
    );
  }
  if (!UUID.test(tenantId) || !UUID.test(clientId)) {
    return invalid("The configured workshop tenant or SPA client ID is invalid.");
  }

  let redirect;
  try {
    redirect = new URL(redirectUri);
  } catch (error) {
    return invalid("The configured SPA redirect URI is invalid.", error);
  }
  if (redirect.protocol !== "https:" && redirect.hostname !== "localhost") {
    return invalid("The configured SPA redirect URI must use HTTPS.");
  }
  if (redirect.search || redirect.hash) {
    return invalid(
        "The configured SPA redirect URI cannot contain a query string or fragment.",
    );
  }

  return Object.freeze({
    ...defaults,
    configured: true,
    tenantId: tenantId.toLowerCase(),
    clientId: clientId.toLowerCase(),
    redirectUri: redirect.href,
  });
}

function tagValue(tags, expectedName) {
  if (!tags || typeof tags !== "object") {
    return undefined;
  }
  const match = Object.entries(tags).find(
    ([name]) => name.toLowerCase() === expectedName.toLowerCase(),
  );
  return match?.[1];
}

export function isWorkshopResourceGroup(group, config) {
  return (
    typeof group?.name === "string" &&
    group.name.startsWith(config.resourceGroupPrefix) &&
    String(tagValue(group.tags, config.environmentTagName) || "").toLowerCase() ===
      config.environmentTagValue.toLowerCase()
  );
}

export function selectEnvironmentResources(resources) {
  if (!Array.isArray(resources)) {
    throw new WorkshopError("Azure returned an invalid workshop resource inventory.");
  }
  const exactType = (type) =>
    resources.filter((resource) => String(resource?.type || "").toLowerCase() === type);
  const matching = (type, pattern) =>
    exactType(type).filter((resource) => pattern.test(String(resource?.name || "")));

  const virtualMachines = matching(
    "microsoft.compute/virtualmachines",
    /^vm-orders-[a-z0-9]+$/i,
  );
  const workspaces = matching(
    "microsoft.operationalinsights/workspaces",
    /^law-[a-z0-9]+$/i,
  );
  const agents = matching("microsoft.app/agents", /^sre-[a-z0-9]+$/i);

  if (virtualMachines.length !== 1) {
    throw new WorkshopError(
      `Expected exactly one workshop VM, but found ${virtualMachines.length}.`,
    );
  }
  if (workspaces.length !== 1) {
    throw new WorkshopError(
      `Expected exactly one workshop Log Analytics workspace, but found ${workspaces.length}.`,
    );
  }
  if (agents.length > 1) {
    throw new WorkshopError(`Expected at most one SRE Agent, but found ${agents.length}.`);
  }
  for (const resource of [...virtualMachines, ...workspaces, ...agents]) {
    if (!RESOURCE_ID.test(String(resource.id || ""))) {
      throw new WorkshopError("Azure returned an unexpected workshop resource ID.");
    }
  }
  return {
    virtualMachine: virtualMachines[0],
    workspace: workspaces[0],
    sreAgent: agents[0] || null,
  };
}

export function createFaultScript(action, requestId) {
  if (!REQUEST_ID.test(requestId)) {
    throw new WorkshopError("The fault request ID is invalid.");
  }
  let argumentsList;
  if (action === "status" || action === "reset") {
    argumentsList = [];
  } else {
    const scenario = SCENARIOS[action];
    if (!scenario) {
      throw new WorkshopError("The requested workshop fault is not supported.");
    }
    argumentsList = scenario.arguments;
  }
  const argumentsText = argumentsList.length ? ` ${argumentsList.join(" ")}` : "";
  return (
    "set -euo pipefail\n" +
    `/usr/bin/python3 /opt/orders-api/faults.py ${action}${argumentsText} ` +
    `--request-id ${requestId}`
  );
}

function runCommandEntries(payload) {
  if (Array.isArray(payload?.value)) {
    return payload.value;
  }
  if (Array.isArray(payload?.properties?.output?.value)) {
    return payload.properties.output.value;
  }
  if (Array.isArray(payload?.properties?.response?.value)) {
    return payload.properties.response.value;
  }
  return null;
}

export function parseRunCommandResult(payload, requestId) {
  if (!REQUEST_ID.test(requestId)) {
    throw new WorkshopError("The fault request ID is invalid.");
  }
  const entries = runCommandEntries(payload);
  if (!entries?.length) {
    throw new WorkshopError("VM Run Command returned no execution status.");
  }

  const messages = [];
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") {
      throw new WorkshopError("VM Run Command returned an invalid execution status.");
    }
    const message = typeof entry.message === "string" ? entry.message : "";
    messages.push(message);
    if (
      String(entry.code || "").toLowerCase().endsWith("/failed") ||
      String(entry.level || "").toLowerCase() === "error"
    ) {
      throw new WorkshopError(
        `VM Run Command failed: ${message.slice(-1500) || "Azure reported a failed status."}`,
      );
    }
  }

  const prefix = `WORKSHOP_RESULT:${requestId}:`;
  const results = messages
    .flatMap((message) => message.split(/\r?\n/))
    .map((line) => line.trimStart())
    .filter((line) => line.startsWith(prefix))
    .map((line) => line.slice(prefix.length));
  if (results.length !== 1) {
    throw new WorkshopError(
      "The VM script did not return one correlated success record. " +
        "Check the Run Command operation before retrying.",
    );
  }

  let result;
  try {
    result = JSON.parse(results[0]);
  } catch (error) {
    throw new WorkshopError("The VM script returned invalid result JSON.", { cause: error });
  }
  if (!result || typeof result !== "object" || result.ok !== true) {
    throw new WorkshopError("The VM script did not verify a successful fault action.");
  }
  return result;
}

export function buildCpuMetricsUrl(vmResourceId, now = new Date()) {
  if (!RESOURCE_ID.test(vmResourceId)) {
    throw new WorkshopError("The selected VM resource ID is invalid.");
  }
  const end = new Date(now);
  const start = new Date(end.getTime() - 30 * 60 * 1000);
  const parameters = new URLSearchParams({
    "api-version": "2023-10-01",
    timespan: `${start.toISOString()}/${end.toISOString()}`,
    interval: "PT1M",
    metricnames: "Percentage CPU",
    aggregation: "Average",
  });
  return `${ARM_ENDPOINT}${vmResourceId}/providers/Microsoft.Insights/metrics?${parameters}`;
}

export function buildDiskQuery(vmResourceId) {
  if (!RESOURCE_ID.test(vmResourceId)) {
    throw new WorkshopError("The selected VM resource ID is invalid.");
  }
  const escapedId = vmResourceId.replaceAll("'", "''");
  return `Perf
| where TimeGenerated > ago(30m)
| where _ResourceId =~ '${escapedId}'
| where ObjectName == 'Logical Disk'
| where CounterName == '% Free Space'
| where InstanceName == '/var/lib/orders'
| summarize AverageFreeSpace = avg(CounterValue) by bin(TimeGenerated, 1m)
| order by TimeGenerated asc`;
}

export function parseMetricSeries(payload) {
  const series = payload?.value?.[0]?.timeseries;
  if (!Array.isArray(series)) {
    throw new WorkshopError("Azure Monitor returned an invalid metric response.");
  }
  const samples = new Map();
  for (const timeline of series) {
    for (const sample of timeline?.data || []) {
      const timestamp = Date.parse(sample?.timeStamp);
      const rawValue = sample?.average;
      const value = rawValue === null || rawValue === "" ? Number.NaN : Number(rawValue);
      if (!Number.isFinite(timestamp) || !Number.isFinite(value)) {
        continue;
      }
      const current = samples.get(timestamp) || [];
      current.push(value);
      samples.set(timestamp, current);
    }
  }
  return [...samples.entries()]
    .sort(([left], [right]) => left - right)
    .map(([timestamp, values]) => ({
      timestamp: new Date(timestamp),
      value: values.reduce((total, value) => total + value, 0) / values.length,
    }));
}

export function parseLogSeries(payload) {
  const tables = payload?.tables;
  if (!Array.isArray(tables) || tables.length !== 1) {
    throw new WorkshopError("Log Analytics returned an unexpected query result.");
  }
  const table = tables[0];
  if (!Array.isArray(table?.columns) || !Array.isArray(table?.rows)) {
    throw new WorkshopError("Log Analytics returned an invalid query table.");
  }
  const columns = table.columns.map((column) => String(column?.name || "").toLowerCase());
  const timeIndex = columns.indexOf("timegenerated");
  const valueIndex = columns.indexOf("averagefreespace");
  if (timeIndex < 0 || valueIndex < 0) {
    throw new WorkshopError("Log Analytics did not return the expected disk columns.");
  }
  return table.rows
    .map((row) => {
      const rawTimestamp = row[timeIndex];
      const rawValue = row[valueIndex];
      return {
        timestamp: new Date(rawTimestamp === null ? Number.NaN : rawTimestamp),
        value: rawValue === null || rawValue === "" ? Number.NaN : Number(rawValue),
      };
    })
    .filter(
      (sample) =>
        Number.isFinite(sample.timestamp.getTime()) && Number.isFinite(sample.value),
    )
    .sort((left, right) => left.timestamp - right.timestamp);
}

export function portalResourceUrl(tenantId, resourceId, suffix = "overview") {
  if (!UUID.test(tenantId) || !RESOURCE_ID.test(resourceId)) {
    throw new WorkshopError("Cannot create a portal link for an invalid Azure resource.");
  }
  return `https://portal.azure.com/#@${tenantId}/resource${resourceId}/${suffix}`;
}

export function sreAgentIncidentsUrl(resourceId) {
  if (
    !RESOURCE_ID.test(resourceId) ||
    !/\/providers\/Microsoft\.App\/agents\//i.test(resourceId)
  ) {
    throw new WorkshopError("Cannot create an SRE Agent link for an invalid resource.");
  }
  return `https://sre.azure.com/agents${resourceId}/views/incidents`;
}

export function formatPercent(value) {
  return `${Number(value).toFixed(1)}%`;
}
