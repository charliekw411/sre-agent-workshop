import {
  ARM_ENDPOINT,
  ARM_SCOPE,
  LOG_ANALYTICS_ENDPOINT,
  LOG_ANALYTICS_SCOPE,
  WorkshopError,
  buildCpuMetricsUrl,
  buildDiskQuery,
  createFaultScript,
  isWorkshopResourceGroup,
  parseLogSeries,
  parseMetricSeries,
  parseRunCommandResult,
  portalResourceUrl,
  selectEnvironmentResources,
  sreAgentIncidentsUrl,
} from "./core.js";

const UUID = /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i;
const RESOURCE_GROUP_ID =
  /^\/subscriptions\/[0-9a-f-]+\/resourceGroups\/[a-z0-9._()-]+$/i;

function sleep(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function requestId() {
  if (typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replaceAll("-", "");
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
}

function retryDelay(response) {
  const value = response.headers.get("Retry-After");
  const seconds = Number(value);
  if (Number.isFinite(seconds)) {
    return Math.min(15, Math.max(2, seconds)) * 1000;
  }
  const date = Date.parse(value || "");
  if (Number.isFinite(date)) {
    return Math.min(15000, Math.max(2000, date - Date.now()));
  }
  return 5000;
}

function azureErrorDetails(payload) {
  const error = payload?.error || payload?.properties?.error || payload;
  return {
    code: typeof error?.code === "string" ? error.code : "",
    message: typeof error?.message === "string" ? error.message : "",
  };
}

export class AzureRequestError extends WorkshopError {
  constructor(message, { status = null, code = "", cause, retryable = false } = {}) {
    super(message, { cause });
    this.name = "AzureRequestError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }

  get permissionDenied() {
    return this.status === 401 || this.status === 403;
  }
}

export class AmbiguousOperationError extends AzureRequestError {
  constructor(message, options = {}) {
    super(message, { ...options, retryable: false });
    this.name = "AmbiguousOperationError";
    this.mayHaveSucceeded = true;
  }
}

export class AzureWorkshopClient {
  constructor(auth, config) {
    this.auth = auth;
    this.config = config;
  }

  assertEndpoint(url, scope) {
    const parsed = new URL(url);
    const expected =
      scope === LOG_ANALYTICS_SCOPE
        ? new URL(LOG_ANALYTICS_ENDPOINT)
        : new URL(ARM_ENDPOINT);
    if (
      parsed.protocol !== "https:" ||
      parsed.origin !== expected.origin ||
      parsed.username ||
      parsed.password
    ) {
      throw new WorkshopError("Refusing a request to an unexpected Azure endpoint.");
    }
    return parsed.href;
  }

  async request(
    url,
    {
      method = "GET",
      body,
      scope = ARM_SCOPE,
      timeoutMs = 30000,
      headers = {},
    } = {},
  ) {
    const safeUrl = this.assertEndpoint(url, scope);
    const token = await this.auth.accessToken(scope);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), timeoutMs);
    let response;
    try {
      response = await fetch(safeUrl, {
        method,
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "omit",
        referrerPolicy: "no-referrer",
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          Authorization: `Bearer ${token}`,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...headers,
        },
      });
    } catch (error) {
      if (error?.name === "AbortError") {
        throw new AzureRequestError(
          `Azure ${method} request timed out after ${Math.ceil(timeoutMs / 1000)} seconds.`,
          { cause: error, retryable: method === "GET" },
        );
      }
      throw new AzureRequestError("The browser could not reach Azure.", {
        cause: error,
        retryable: method === "GET",
      });
    } finally {
      window.clearTimeout(timer);
    }

    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }
    if (!response.ok) {
      const details = azureErrorDetails(payload);
      let message = details.message || `Azure returned HTTP ${response.status}.`;
      if (response.status === 401) {
        message = "Azure authentication expired or was rejected. Sign in again.";
      } else if (response.status === 403) {
        message =
          "Azure denied this operation. Check the participant's RBAC assignments and " +
          "the SPA API permissions.";
      } else if (response.status === 429) {
        message = "Azure throttled this request. Wait before trying again.";
      }
      throw new AzureRequestError(message.slice(0, 1500), {
        status: response.status,
        code: details.code,
        retryable: response.status === 408 || response.status === 429 || response.status >= 500,
      });
    }
    return { payload, response };
  }

  async listAll(url) {
    const values = [];
    let next = url;
    while (next) {
      const { payload } = await this.request(next);
      if (!Array.isArray(payload?.value)) {
        throw new AzureRequestError("Azure returned an invalid paged resource response.");
      }
      values.push(...payload.value);
      next = payload.nextLink || "";
      if (next) {
        this.assertEndpoint(next, ARM_SCOPE);
      }
    }
    return values;
  }

  async discoverEnvironments() {
    const subscriptions = (
      await this.listAll(`${ARM_ENDPOINT}/subscriptions?api-version=2022-12-01`)
    )
      .filter(
        (subscription) =>
          String(subscription?.state || "").toLowerCase() === "enabled" &&
          String(subscription?.tenantId || "").toLowerCase() === this.config.tenantId,
      )
      .map((subscription) => ({
        id: String(subscription.subscriptionId || ""),
        name: String(subscription.displayName || subscription.subscriptionId || ""),
        tenantId: String(subscription.tenantId || "").toLowerCase(),
      }))
      .filter((subscription) => UUID.test(subscription.id));

    const groupsBySubscription = await Promise.all(
      subscriptions.map(async (subscription) => ({
        subscription,
        groups: await this.listAll(
          `${ARM_ENDPOINT}/subscriptions/${subscription.id}/resourcegroups?api-version=2021-04-01`,
        ),
      })),
    );
    return groupsBySubscription
      .flatMap(({ subscription, groups }) =>
        groups
          .filter((group) => isWorkshopResourceGroup(group, this.config))
          .map((group) => ({
            subscription,
            resourceGroup: {
              id: String(group.id || ""),
              name: String(group.name || ""),
              location: String(group.location || ""),
              tags: group.tags || {},
            },
          })),
      )
      .sort(
        (left, right) =>
          left.subscription.name.localeCompare(right.subscription.name) ||
          left.resourceGroup.name.localeCompare(right.resourceGroup.name),
      );
  }

  async loadEnvironment(candidate) {
    const groupId = candidate?.resourceGroup?.id;
    if (
      typeof groupId !== "string" ||
      !RESOURCE_GROUP_ID.test(groupId) ||
      !groupId.toLowerCase().startsWith(
        `/subscriptions/${candidate.subscription.id}/resourcegroups/`.toLowerCase(),
      )
    ) {
      throw new WorkshopError("The selected workshop resource group is invalid.");
    }
    const resources = await this.listAll(
      `${ARM_ENDPOINT}${groupId}/resources?api-version=2021-04-01`,
    );
    const selected = selectEnvironmentResources(resources);
    const workspaceResponse = await this.request(
      `${ARM_ENDPOINT}${selected.workspace.id}?api-version=2023-09-01`,
    );
    const workspaceCustomerId = String(
      workspaceResponse.payload?.properties?.customerId || "",
    );
    if (!UUID.test(workspaceCustomerId)) {
      throw new WorkshopError(
        "The selected Log Analytics workspace did not return a valid workspace ID.",
      );
    }

    const alertRoot = `${groupId}/providers/Microsoft.Insights`;
    return Object.freeze({
      subscription: candidate.subscription,
      resourceGroup: candidate.resourceGroup,
      virtualMachine: selected.virtualMachine,
      workspace: {
        ...selected.workspace,
        customerId: workspaceCustomerId,
      },
      sreAgent: selected.sreAgent,
      alerts: Object.freeze({
        cpu: `${alertRoot}/metricAlerts/alert-orders-high-cpu`,
        disk: `${alertRoot}/scheduledQueryRules/alert-orders-data-disk-free`,
      }),
    });
  }

  async runFault(environment, action) {
    const correlationId = requestId();
    const script = createFaultScript(action, correlationId);
    const url =
      `${ARM_ENDPOINT}${environment.virtualMachine.id}/runCommand` +
      "?api-version=2024-11-01";
    let operation;
    try {
      operation = await this.request(url, {
        method: "POST",
        timeoutMs: 240000,
        body: {
          commandId: "RunShellScript",
          script: script.split("\n"),
        },
      });
    } catch (error) {
      if (error instanceof AzureRequestError && error.status === null) {
        throw new AmbiguousOperationError(
          "The Run Command response was interrupted. The fault may still have started; " +
            "check status before running it again.",
          { cause: error },
        );
      }
      throw error;
    }

    if (operation.response.status === 200) {
      return parseRunCommandResult(operation.payload, correlationId);
    }
    if (operation.response.status !== 202) {
      throw new AzureRequestError(
        `VM Run Command returned unexpected HTTP ${operation.response.status}.`,
        { status: operation.response.status },
      );
    }

    const location = operation.response.headers.get("Location");
    if (!location) {
      throw new AmbiguousOperationError(
        "Azure accepted Run Command without a status URL. Check fault status before retrying.",
      );
    }
    this.assertEndpoint(location, ARM_SCOPE);
    const deadline = Date.now() + 240000;
    let delay = retryDelay(operation.response);
    while (Date.now() < deadline) {
      await sleep(delay);
      let poll;
      try {
        poll = await this.request(location, {
          timeoutMs: Math.min(30000, Math.max(1000, deadline - Date.now())),
        });
      } catch (error) {
        throw new AmbiguousOperationError(
          "Azure accepted Run Command, but its final status could not be read. " +
            "Check fault status before retrying.",
          { cause: error },
        );
      }
      const status = String(poll.payload?.status || "").toLowerCase();
      if (poll.response.status === 202 || status === "inprogress" || status === "running") {
        delay = retryDelay(poll.response);
        continue;
      }
      if (status === "failed" || status === "canceled") {
        const details = azureErrorDetails(poll.payload);
        throw new AzureRequestError(
          details.message || `VM Run Command finished with status ${status}.`,
          { code: details.code },
        );
      }
      try {
        return parseRunCommandResult(poll.payload, correlationId);
      } catch (error) {
        throw new AmbiguousOperationError(
          "Azure finished Run Command without its correlated result. " +
            "Check fault status before retrying.",
          { cause: error },
        );
      }
    }
    throw new AmbiguousOperationError(
      "VM Run Command exceeded four minutes. It may still have completed; " +
        "check fault status before retrying.",
    );
  }

  async cpuSeries(environment) {
    const { payload } = await this.request(
      buildCpuMetricsUrl(environment.virtualMachine.id),
    );
    return parseMetricSeries(payload);
  }

  async diskSeries(environment) {
    const { payload } = await this.request(
      `${LOG_ANALYTICS_ENDPOINT}/v1/workspaces/${environment.workspace.customerId}/query`,
      {
        method: "POST",
        scope: LOG_ANALYTICS_SCOPE,
        timeoutMs: 30000,
        body: {
          query: buildDiskQuery(environment.virtualMachine.id),
          timespan: "PT30M",
        },
      },
    );
    return parseLogSeries(payload);
  }

  links(environment, scenario) {
    const alertId = scenario === "cpu" ? environment.alerts.cpu : environment.alerts.disk;
    const chartResource =
      scenario === "cpu" ? environment.virtualMachine.id : environment.workspace.id;
    return {
      chart: portalResourceUrl(
        this.config.tenantId,
        chartResource,
        scenario === "cpu" ? "metrics" : "overview",
      ),
      alert: portalResourceUrl(this.config.tenantId, alertId),
      investigation:
        environment.sreAgent
          ? sreAgentIncidentsUrl(environment.sreAgent.id)
          : "",
    };
  }
}
