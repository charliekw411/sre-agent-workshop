import { SCENARIOS, formatPercent } from "./core.js";
import { element } from "./dom.js";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function actionButton(label, className = "") {
  const button = element("button", className, label);
  button.type = "button";
  return button;
}

function setHidden(node, hidden) {
  node.hidden = hidden;
  node.setAttribute("aria-hidden", String(hidden));
}

function accountName(account) {
  return account?.name || account?.username || "Workshop account";
}

export class HeaderConnection {
  constructor(actions) {
    this.actions = actions;
    this.root = null;
    this.state = null;
    this.handleDocumentClick = (event) => {
      if (this.root && !this.root.contains(event.target)) {
        this.close();
      }
    };
  }

  mount() {
    const header = document.querySelector(".md-header__inner");
    if (!header) {
      return false;
    }
    const existing = header.querySelector(".sre-connection");
    if (existing) {
      this.root = existing;
      return true;
    }

    this.root = element("div", "sre-connection");
    this.button = actionButton("", "sre-connection__button");
    this.button.setAttribute("aria-haspopup", "true");
    this.button.setAttribute("aria-expanded", "false");
    this.dot = element("span", "sre-connection__dot");
    this.dot.setAttribute("aria-hidden", "true");
    this.label = element("span", "sre-connection__label");
    this.button.append(this.dot, this.label);

    this.panel = element("section", "sre-connection__panel");
    this.panel.hidden = true;
    this.panel.setAttribute("aria-label", "Azure workshop connection");
    this.message = element("p", "sre-connection__message");
    this.message.setAttribute("role", "status");
    this.details = element("dl", "sre-connection__details");
    this.fields = {};
    for (const [key, label] of [
      ["account", "Account"],
      ["tenant", "Tenant"],
      ["subscription", "Subscription"],
      ["environment", "Environment"],
    ]) {
      const term = element("dt", "", label);
      const value = element("dd");
      this.fields[key] = value;
      this.details.append(term, value);
    }
    this.actionsRoot = element("div", "sre-connection__actions");
    this.ordersGui = element(
      "a",
      "md-button md-button--primary",
      "Open Orders GUI",
    );
    this.ordersGui.target = "_blank";
    this.ordersGui.rel = "noopener noreferrer";
    setHidden(this.ordersGui, true);
    this.retry = actionButton("Retry connection", "md-button md-button--primary");
    this.change = actionButton("Change environment", "md-button");
    this.disconnect = actionButton("Disconnect", "md-button");
    this.actionsRoot.append(this.ordersGui, this.retry, this.change, this.disconnect);
    this.panel.append(this.message, this.details, this.actionsRoot);
    this.root.append(this.button, this.panel);

    const source = header.querySelector(".md-header__source");
    header.insertBefore(this.root, source || null);
    this.button.addEventListener("click", () => this.activate());
    this.retry.addEventListener("click", () => {
      this.close();
      this.actions.retry();
    });
    this.change.addEventListener("click", () => {
      this.close();
      this.actions.changeEnvironment();
    });
    this.disconnect.addEventListener("click", () => {
      this.close();
      this.actions.disconnect();
    });
    document.addEventListener("click", this.handleDocumentClick);
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        this.close();
      }
    });
    if (this.state) {
      this.render(this.state);
    }
    return true;
  }

  activate() {
    if (!this.state || this.state.phase === "connecting") {
      return;
    }
    if (this.state.phase === "not-connected") {
      this.actions.connect();
      return;
    }
    const opening = this.panel.hidden;
    this.panel.hidden = !opening;
    this.button.setAttribute("aria-expanded", String(opening));
  }

  close() {
    if (!this.panel || !this.button) {
      return;
    }
    this.panel.hidden = true;
    this.button.setAttribute("aria-expanded", "false");
  }

  render(state) {
    this.state = state;
    if (!this.root) {
      return;
    }
    const labels = {
      "not-connected": "Not connected",
      connecting: "Connecting...",
      connected: "Connected",
      error: "Connection error",
    };
    this.root.dataset.state = state.phase;
    this.label.textContent = labels[state.phase] || "Connection error";
    this.button.disabled = state.phase === "connecting";
    this.button.title =
      state.phase === "not-connected"
        ? "Sign in with the provisioned workshop account"
        : labels[state.phase] || "Azure connection";

    const environment = state.environment;
    const showDetails = Boolean(state.account || environment);
    setHidden(this.details, !showDetails);
    this.fields.account.textContent = state.account ? accountName(state.account) : "-";
    this.fields.tenant.textContent = state.configuredTenant || "-";
    this.fields.subscription.textContent = environment
      ? `${environment.subscription.name} (${environment.subscription.id})`
      : "-";
    this.fields.environment.textContent = environment?.resourceGroup?.name || "-";

    this.message.textContent =
      state.message ||
      (state.phase === "connected"
        ? "Azure controls and telemetry are enabled for the selected environment."
        : "Sign in with the provisioned workshop account to enable Azure controls.");
    const ordersGuiUrl =
      state.phase === "connected" ? environment?.ordersGuiUrl || "" : "";
    this.ordersGui.href = ordersGuiUrl || "#";
    setHidden(this.ordersGui, !ordersGuiUrl);
    setHidden(this.retry, state.phase !== "error" || !state.canRetry);
    setHidden(this.change, !state.account || !state.environment);
    setHidden(this.disconnect, !state.account);
  }
}

export class EnvironmentPicker {
  constructor() {
    this.dialog = element("dialog", "sre-environment-dialog");
    this.form = element("form");
    this.form.method = "dialog";
    const heading = element("h2", "", "Select workshop environment");
    const description = element(
      "p",
      "",
      "Choose the subscription and tagged workshop environment that these controls should target.",
    );

    const subscriptionLabel = element("label", "", "Subscription");
    this.subscription = element("select");
    this.subscription.required = true;
    subscriptionLabel.append(this.subscription);

    const environmentLabel = element("label", "", "Workshop environment");
    this.environment = element("select");
    this.environment.required = true;
    environmentLabel.append(this.environment);

    const actions = element("div", "sre-environment-dialog__actions");
    this.cancel = actionButton("Cancel", "md-button");
    this.confirm = actionButton("Use environment", "md-button md-button--primary");
    actions.append(this.cancel, this.confirm);
    this.form.append(heading, description, subscriptionLabel, environmentLabel, actions);
    this.dialog.append(this.form);
    document.body.append(this.dialog);
    this.subscription.addEventListener("change", () => this.populateEnvironments());
    this.form.addEventListener("submit", (event) => {
      event.preventDefault();
      this.confirm.click();
    });
  }

  populateEnvironments() {
    const subscriptionId = this.subscription.value;
    this.environment.replaceChildren();
    this.candidates
      .filter((candidate) => candidate.subscription.id === subscriptionId)
      .forEach((candidate) => {
        const option = element("option");
        option.value = candidate.resourceGroup.id;
        option.textContent = candidate.resourceGroup.name;
        this.environment.append(option);
      });
  }

  choose(candidates) {
    this.candidates = candidates;
    this.subscription.replaceChildren();
    const subscriptions = new Map(
      candidates.map((candidate) => [candidate.subscription.id, candidate.subscription]),
    );
    for (const subscription of subscriptions.values()) {
      const option = element("option");
      option.value = subscription.id;
      option.textContent = `${subscription.name} (${subscription.id})`;
      this.subscription.append(option);
    }
    this.populateEnvironments();

    return new Promise((resolve) => {
      let settled = false;
      const finish = (candidate) => {
        if (settled) {
          return;
        }
        settled = true;
        this.confirm.removeEventListener("click", confirm);
        this.cancel.removeEventListener("click", cancel);
        this.dialog.removeEventListener("cancel", cancel);
        this.dialog.close();
        resolve(candidate);
      };
      const confirm = () => {
        const selected = candidates.find(
          (candidate) =>
            candidate.subscription.id === this.subscription.value &&
            candidate.resourceGroup.id === this.environment.value,
        );
        finish(selected || null);
      };
      const cancel = (event) => {
        event?.preventDefault();
        finish(null);
      };
      this.confirm.addEventListener("click", confirm);
      this.cancel.addEventListener("click", cancel);
      this.dialog.addEventListener("cancel", cancel);
      this.dialog.showModal();
    });
  }
}

function svgElement(tag, attributes = {}, text = "") {
  const node = document.createElementNS(SVG_NAMESPACE, tag);
  for (const [name, value] of Object.entries(attributes)) {
    node.setAttribute(name, String(value));
  }
  if (text) {
    node.textContent = text;
  }
  return node;
}

function timeLabel(date, includeSeconds = false) {
  return new Intl.DateTimeFormat([], {
    hour: "2-digit",
    minute: "2-digit",
    ...(includeSeconds ? { second: "2-digit" } : {}),
    hour12: false,
  }).format(date);
}

export function renderLineChart(
  root,
  points,
  scenario,
  now = new Date(),
  incidentRequestedAt = null,
) {
  root.replaceChildren();

  const width = 720;
  const height = 280;
  const margin = { top: 18, right: 24, bottom: 42, left: 52 };
  const chartWidth = width - margin.left - margin.right;
  const chartHeight = height - margin.top - margin.bottom;
  const end = now.getTime();
  const start = end - 30 * 60 * 1000;
  const x = (timestamp) =>
    margin.left + ((timestamp.getTime() - start) / (end - start)) * chartWidth;
  const y = (value) => margin.top + ((100 - Math.max(0, Math.min(100, value))) / 100) * chartHeight;
  const visible = points.filter(
    (point) =>
      point.timestamp.getTime() >= start - 60000 && point.timestamp.getTime() <= end + 60000,
  );
  const incidentTimestamp = incidentRequestedAt?.getTime();
  const showIncident =
    Number.isFinite(incidentTimestamp) &&
    incidentTimestamp >= start &&
    incidentTimestamp <= end;
  if (!visible.length && !showIncident) {
    root.append(
      element(
        "p",
        "sre-chart__empty",
        points.length
          ? "Azure returned no samples in the last 30 minutes."
          : "No samples are available yet. Azure Monitor ingestion can take several minutes.",
      ),
    );
    return;
  }

  const summary = element("p", "sre-chart__summary");
  const summaryParts = [];
  if (showIncident) {
    summaryParts.push(`Fault requested ${timeLabel(incidentRequestedAt)}`);
  }
  if (visible.length) {
    const values = visible.map((point) => point.value);
    const latest = visible[visible.length - 1];
    summaryParts.push(
      scenario.id === "cpu"
        ? `Latest ${formatPercent(latest.value)} | Peak ${formatPercent(Math.max(...values))}`
        : `Latest ${formatPercent(latest.value)} failed | Peak ${formatPercent(
            Math.max(...values),
          )} failed`,
    );
    summaryParts.push(`Sample time ${timeLabel(latest.timestamp)}`);
  } else {
    summaryParts.push("Waiting for Azure Monitor samples");
  }
  summary.textContent = summaryParts.join(" | ");
  root.append(summary);

  const svg = svgElement("svg", {
    class: "sre-chart__svg",
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `${scenario.chartTitle}, last 30 minutes. ${summary.textContent}`,
  });
  for (const tick of [0, 25, 50, 75, 100]) {
    const position = y(tick);
    svg.append(
      svgElement("line", {
        class: "sre-chart__grid",
        x1: margin.left,
        x2: width - margin.right,
        y1: position,
        y2: position,
      }),
      svgElement(
        "text",
        {
          class: "sre-chart__axis-label",
          x: margin.left - 9,
          y: position + 4,
          "text-anchor": "end",
        },
        `${tick}%`,
      ),
    );
  }

  const thresholdY = y(scenario.threshold);
  svg.append(
    svgElement("line", {
      class: "sre-chart__threshold",
      x1: margin.left,
      x2: width - margin.right,
      y1: thresholdY,
      y2: thresholdY,
    }),
    svgElement(
      "text",
      {
        class: "sre-chart__threshold-label",
        x: width - margin.right,
        y: thresholdY - 7,
        "text-anchor": "end",
      },
      scenario.thresholdLabel,
    ),
  );

  if (showIncident) {
    const incidentX = x(incidentRequestedAt);
    const labelOnLeft = incidentX > width - margin.right - 120;
    svg.append(
      svgElement("line", {
        class: "sre-chart__event",
        x1: incidentX,
        x2: incidentX,
        y1: margin.top,
        y2: height - margin.bottom,
      }),
      svgElement(
        "text",
        {
          class: "sre-chart__event-label",
          x: incidentX + (labelOnLeft ? -6 : 6),
          y: margin.top + 13,
          "text-anchor": labelOnLeft ? "end" : "start",
        },
        "Fault requested",
      ),
    );
  }

  if (visible.length) {
    const latest = visible[visible.length - 1];
    const path = visible
      .map((point, index) => `${index ? "L" : "M"} ${x(point.timestamp).toFixed(2)} ${y(point.value).toFixed(2)}`)
      .join(" ");
    svg.append(
      svgElement("path", {
        class: "sre-chart__line",
        d: path,
      }),
      svgElement("circle", {
        class: "sre-chart__latest",
        cx: x(latest.timestamp),
        cy: y(latest.value),
        r: 4,
      }),
    );
  }

  for (const [timestamp, anchor] of [
    [new Date(start), "start"],
    [new Date(start + 15 * 60 * 1000), "middle"],
    [new Date(end), "end"],
  ]) {
    svg.append(
      svgElement(
        "text",
        {
          class: "sre-chart__axis-label",
          x: x(timestamp),
          y: height - 13,
          "text-anchor": anchor,
        },
        timeLabel(timestamp),
      ),
    );
  }
  root.append(svg);
}

function describeResult(action, result) {
  if (action === "reset") {
    return (
      "All-scenarios reset completed. PostgreSQL access is allowed, CPU fault units are " +
      "inactive, and the recycled Orders API passed readiness and order probes."
    );
  }
  if (action === "reset-cpu") {
    return "CPU incident reset completed without changing the PostgreSQL fault rule.";
  }
  if (action === "reset-postgresql") {
    return (
      "PostgreSQL incident reset completed. Access is allowed and the recycled " +
      "Orders API passed readiness and order probes; the CPU fault was not changed."
    );
  }
  if (action === "status") {
    return `Fault status: CPU ${result.cpu || "unknown"}, PostgreSQL ${
      result.postgresql || "unknown"
    } (${result.postgresqlAccess || "unknown"}), application connectivity ${
      result.postgresqlConnectivity || "unknown"
    }.`;
  }
  if (action === "cpu") {
    return `CPU incident started for ${result.seconds || 600} seconds with ${
      result.workers || 2
    } workers.`;
  }
  return (
    "Fault confirmed: PostgreSQL access was blocked by the scoped outbound NSG rule. " +
    "The Orders API was recycled and liveness 200 plus readiness/orders 503 were verified. " +
    "Continue to Task 4: open the alert rule's History and SRE Agent incidents " +
    "to follow automatic diagnosis and repair. Telemetry and alert delivery can lag."
  );
}

function describeOperation(action) {
  if (action === "reset-cpu") {
    return "Resetting only the CPU fault...";
  }
  if (action === "reset-postgresql") {
    return "Restoring PostgreSQL access and verifying the recycled Orders API...";
  }
  if (action === "reset") {
    return "Resetting all workshop fault scenarios...";
  }
  if (action === "status") {
    return "Checking the VM and PostgreSQL network fault status...";
  }
  if (action === "cpu") {
    return "Starting CPU fault through Azure VM Run Command. Confirmation can take a few minutes.";
  }
  return "Starting the bounded PostgreSQL fault and verifying application connectivity...";
}

export class IncidentLauncher {
  constructor(root, app, scenarioId) {
    this.root = root;
    this.app = app;
    this.scenario = SCENARIOS[scenarioId];
    this.refreshSeconds = scenarioId === "cpu"
      ? Math.min(app.config.refreshSeconds, 15)
      : app.config.refreshSeconds;
    this.environmentKey = "";
    this.incidentRequestedAt = null;
    this.incidentPhase = "";
    this.points = [];
    this.refreshing = false;
    this.refreshVersion = 0;
    this.telemetryError = false;
    this.nextRefreshAt = null;
    this.timer = null;
    this.executingAction = null;
    this.progressTimer = null;
    this.build();
  }

  build() {
    this.root.classList.add("sre-incident-launcher");
    this.root.replaceChildren();
    const heading = element("div", "sre-launcher__heading");
    const title = element("h3", "", this.scenario.chartTitle);
    const badge = element("span", "sre-launcher__badge", "Authenticated Azure control");
    heading.append(title, badge);

    const description = element(
      "p",
      "sre-launcher__description",
      "Select once. The launcher checks the VM, blocks the scoped network rule, " +
        "then recycles and verifies only the Orders API. This can take several minutes; " +
        "monitoring and the agent update separately.",
    );
    setHidden(description, this.scenario.id !== "postgresql");

    const actions = element("div", "sre-launcher__actions");
    this.run = actionButton(this.scenario.runLabel, "md-button md-button--primary");
    this.reset = actionButton("Reset incident", "md-button");
    actions.append(this.run, this.reset);

    this.status = element("p", "sre-launcher__status");
    this.status.setAttribute("role", "status");
    this.status.setAttribute("aria-live", "polite");
    this.status.textContent = "Connect to Azure from the site header to enable controls.";

    this.progress = element("section", "sre-launcher__progress");
    this.progress.setAttribute(
      "aria-label",
      this.scenario.id === "cpu" ? "CPU incident progress" : "PostgreSQL fault progress",
    );
    setHidden(this.progress, true);
    const progressHeading = element("div", "sre-launcher__progress-heading");
    this.progressTitle = element("strong");
    this.progressTitle.setAttribute("role", "status");
    setHidden(this.progressTitle, this.scenario.id !== "cpu");
    const progressBar = element("progress");
    progressBar.setAttribute("aria-label", "Azure fault operation in progress");
    setHidden(progressBar, this.scenario.id !== "postgresql");
    this.elapsed = element("span", "sre-launcher__elapsed");
    this.elapsed.setAttribute("role", "timer");
    this.elapsed.setAttribute("aria-live", "off");
    this.elapsed.title = this.scenario.id === "cpu"
      ? "Time since you requested the CPU fault"
      : "Time since you requested this PostgreSQL operation";
    progressHeading.append(this.progressTitle, progressBar, this.elapsed);
    this.progressSteps = element("ol", "sre-launcher__steps");
    this.steps = ["Start CPU fault", "Observe CPU telemetry", "Check Azure alert"].map(
      (label) => {
        const step = element("li", "", label);
        this.progressSteps.append(step);
        return step;
      },
    );
    setHidden(this.progressSteps, this.scenario.id !== "cpu");
    this.progressDetail = element("p", "sre-launcher__progress-detail");
    setHidden(this.progressDetail, this.scenario.id !== "cpu");
    this.progress.append(progressHeading, this.progressSteps, this.progressDetail);

    const graphHeader = element("div", "sre-chart__header");
    const graphTitle = element("h4", "", `${this.scenario.chartTitle} | Last 30 minutes`);
    const graphControls = element("div", "sre-chart__controls");
    this.live = element("span", "sre-chart__live", "Offline");
    this.refreshCountdown = element("span", "sre-chart__countdown");
    this.refreshCountdown.setAttribute("role", "timer");
    this.refreshCountdown.setAttribute("aria-live", "off");
    this.refresh = actionButton("Refresh now", "md-button");
    this.refresh.title =
      "Read the latest Azure Monitor samples. Refreshing does not speed up ingestion.";
    graphControls.append(this.live, this.refreshCountdown, this.refresh);
    this.graphStatus = element(
      "p",
      "sre-chart__refresh",
      "Connect to Azure to load telemetry.",
    );
    this.graphStatus.setAttribute("role", "status");
    graphHeader.append(graphTitle, graphControls);
    this.graph = element("div", "sre-chart");
    this.graph.append(
      element("p", "sre-chart__empty", "Connect to Azure to load telemetry."),
    );

    const graphHint = element(
      "p",
      "sre-launcher__safety",
      "Read the newest sample time, not just the line. PostgreSQL samples can arrive " +
        "several minutes late. A point above the alert line shows failed database calls, " +
        "not that an alert has fired or the agent has started. Check the alert rule's " +
        "History, then SRE Agent incidents. Older failures remain after recovery.",
    );
    setHidden(graphHint, this.scenario.id !== "postgresql");

    this.links = element("nav", "sre-launcher__links");
    this.links.setAttribute("aria-label", "Incident investigation links");
    this.ordersLink = element("a", "", "Open Orders GUI");
    this.alertLink = element("a", "", "Open alert rule");
    this.agentLink = element("a", "", "Open SRE Agent incidents");
    for (const link of [this.ordersLink, this.alertLink, this.agentLink]) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    this.links.append(this.ordersLink, this.alertLink, this.agentLink);
    setHidden(this.links, true);

    this.root.append(
      heading,
      description,
      actions,
      this.status,
      this.progress,
      graphHeader,
      this.graphStatus,
      this.graph,
      graphHint,
      this.links,
    );

    this.run.addEventListener("click", () => this.execute(this.scenario.action));
    this.reset.addEventListener("click", () =>
      this.execute(`reset-${this.scenario.id}`),
    );
    this.refresh.addEventListener("click", () => this.refreshGraph());
  }

  async execute(action) {
    const environment = this.app.state.environment;
    this.executingAction = action;
    this.startProgressTimer(new Date());
    if (action === this.scenario.action) {
      this.incidentRequestedAt = new Date();
      this.incidentPhase = "starting";
      this.renderGraph();
    } else if (action === "reset-cpu") {
      this.incidentPhase = "resetting";
    }
    this.status.dataset.kind = "progress";
    this.status.textContent = describeOperation(action);
    this.renderCpuProgress();
    try {
      const result = await this.app.executeFault(action, (message) => {
        if (!this.app.connected || environment !== this.app.state.environment) {
          return;
        }
        this.status.dataset.kind = "progress";
        this.status.textContent = message;
      });
      if (!this.app.connected || environment !== this.app.state.environment) {
        return;
      }
      this.incidentPhase = action === "reset-cpu" ? "reset" : "running";
      this.status.dataset.kind = "success";
      this.status.textContent = describeResult(action, result);
      this.renderCpuProgress();
    } catch (error) {
      if (!this.app.connected || environment !== this.app.state.environment) {
        return;
      }
      this.incidentPhase = action === "reset-cpu" ? "reset-error" : "start-error";
      this.status.dataset.kind = "error";
      this.status.textContent = this.app.errorMessage(error);
      this.renderCpuProgress();
      this.app.reportError(error);
      return;
    } finally {
      this.executingAction = null;
      this.stopProgressTimer();
    }
    await this.refreshGraph();
  }

  update(state) {
    const connected = state.phase === "connected" && state.environment;
    const busy = Boolean(state.operation);
    this.reset.disabled = !connected || busy;
    this.refresh.disabled = !connected || this.refreshing;
    this.run.disabled = !connected || busy || state.requiresStatusCheck;
    const starting = state.operation?.action === this.scenario.action;
    this.run.textContent = starting
      ? this.scenario.id === "cpu"
        ? "Starting CPU incident..."
        : "Blocking PostgreSQL..."
      : this.scenario.runLabel;
    this.run.setAttribute("aria-busy", String(starting));

    if (!connected) {
      this.stopTimer();
      this.environmentKey = "";
      this.refreshVersion += 1;
      this.refreshing = false;
      this.nextRefreshAt = null;
      this.telemetryError = false;
      this.incidentRequestedAt = null;
      this.incidentPhase = "";
      this.points = [];
      this.graph.replaceChildren(
        element("p", "sre-chart__empty", "Connect to Azure to load telemetry."),
      );
      this.graph.setAttribute("aria-busy", "false");
      this.graphStatus.dataset.kind = "";
      this.graphStatus.textContent = "Connect to Azure to load telemetry.";
      this.renderCpuProgress();
      this.renderRefreshStatus();
      setHidden(this.links, true);
      if (state.phase === "connecting") {
        this.status.textContent = "Connecting to Azure and discovering workshop environments...";
      } else if (state.phase === "error") {
        this.status.dataset.kind = "error";
        this.status.textContent = state.message;
      } else {
        this.status.dataset.kind = "";
        this.status.textContent = "Connect to Azure from the site header to enable controls.";
      }
      return;
    }

    const key = `${state.environment.subscription.id}:${state.environment.resourceGroup.id}`;
    if (key !== this.environmentKey) {
      this.stopProgressTimer();
      this.environmentKey = key;
      this.refreshVersion += 1;
      this.refreshing = false;
      this.nextRefreshAt = null;
      this.telemetryError = false;
      this.incidentRequestedAt = null;
      this.incidentPhase = "";
      this.points = [];
      this.renderGraph();
      this.status.dataset.kind = "success";
      this.status.textContent =
        `Targeting ${state.environment.resourceGroup.name} / ` +
        `${state.environment.virtualMachine.name} / ` +
        `${state.environment.postgresqlServer.name}.`;
      const links = this.app.incidentLinks(this.scenario.id);
      this.ordersLink.href = state.environment.ordersGuiUrl || "#";
      setHidden(this.ordersLink, !state.environment.ordersGuiUrl);
      this.alertLink.href = links.alert || "#";
      setHidden(this.alertLink, !links.alert);
      this.agentLink.href = links.investigation || "#";
      setHidden(this.agentLink, !links.investigation);
      setHidden(this.links, false);
      void this.refreshGraph();
    }
    if (busy && !this.executingAction) {
      this.status.dataset.kind = "progress";
      this.status.textContent = describeOperation(state.operation.action);
    }
    this.startTimer();
    this.renderCpuProgress();
    this.renderRefreshStatus();
  }

  startTimer() {
    if (this.timer) {
      return;
    }
    this.timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && this.root.isConnected) {
        const now = Date.now();
        this.renderCpuProgress(new Date(now));
        if (!this.refreshing && now >= this.nextRefreshAt) {
          void this.refreshGraph();
        }
        this.renderRefreshStatus(now);
      }
    }, 1000);
  }

  stopTimer() {
    if (this.timer) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    this.stopProgressTimer();
  }

  startProgressTimer(startedAt) {
    if (this.scenario.id !== "postgresql") {
      return;
    }
    this.stopProgressTimer();
    const updateElapsed = () => {
      const seconds = Math.max(0, Math.floor((Date.now() - startedAt.getTime()) / 1000));
      const minutes = String(Math.floor(seconds / 60)).padStart(2, "0");
      const remainder = String(seconds % 60).padStart(2, "0");
      this.elapsed.textContent =
        `Elapsed ${minutes}:${remainder}. Azure operations can take several minutes. ` +
        "Do not repeat the action while it is running.";
    };
    updateElapsed();
    setHidden(this.progress, false);
    this.progressTimer = window.setInterval(updateElapsed, 1000);
  }

  stopProgressTimer() {
    if (this.scenario.id !== "postgresql") {
      return;
    }
    if (this.progressTimer !== null) {
      window.clearInterval(this.progressTimer);
      this.progressTimer = null;
    }
    setHidden(this.progress, true);
  }

  renderCpuProgress(now = new Date()) {
    if (this.scenario.id !== "cpu") {
      return;
    }
    const visible =
      this.app.connected &&
      Boolean(this.incidentRequestedAt);
    setHidden(this.progress, !visible);
    if (!visible) {
      return;
    }

    const seconds = Math.max(
      0,
      Math.floor((now.getTime() - this.incidentRequestedAt.getTime()) / 1000),
    );
    this.elapsed.textContent =
      `Elapsed ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
    this.progress.dataset.phase = this.incidentPhase;
    let currentStep = 0;
    let title = "Starting CPU fault";
    let detail =
      "Azure VM Run Command is confirming your request. This can take a few minutes; " +
      "the workers may already be running. Keep this page open and use Refresh now " +
      "to check telemetry. Do not run the fault again while waiting.";

    if (this.incidentPhase === "running") {
      currentStep = 1;
      const samples = this.points.filter(
        (point) =>
          point.timestamp >= this.incidentRequestedAt && point.timestamp <= now,
      );
      const latest = samples[samples.length - 1];
      const observedHighCpu = samples.some(
        (point) => point.value > this.scenario.threshold,
      );
      title = "CPU fault confirmed; waiting for telemetry";
      detail =
        "Azure confirmed the CPU workers started. New one-minute samples can take " +
        "a few minutes to appear. Refresh now checks for available data but cannot " +
        "speed up Azure Monitor ingestion. Start your own investigation in Task 4.";
      if (latest) {
        title = "CPU fault confirmed; waiting for high CPU";
        detail =
          `Latest one-minute CPU sample: ${formatPercent(latest.value)} at ` +
          `${timeLabel(latest.timestamp)}. Watch for a sample above 80% while you ` +
          "investigate in Task 4. Do not restart the fault to make telemetry arrive sooner.";
      }
      if (observedHighCpu) {
        currentStep = 2;
        title = latest.value > this.scenario.threshold
          ? "High CPU observed; check alert history"
          : "CPU sample below threshold; check recovery";
        detail =
          `Latest one-minute CPU sample: ${formatPercent(latest.value)} at ` +
          `${timeLabel(latest.timestamp)}. The Azure alert evaluates a five-minute ` +
          "average every minute, so one sample above 80% does not confirm it fired. " +
          "Allow about 5 to 10 minutes from the fault starting, then check History " +
          "in Task 4 and the SRE Agent incident. If CPU has fallen, also verify recovery in Task 6.";
      } else if (seconds >= 5 * 60) {
        detail +=
          " If CPU still has not risen, compare the VM's Percentage CPU metric " +
          "in Azure and ask your facilitator before retrying.";
      }
    } else if (this.incidentPhase === "resetting") {
      title = "Resetting CPU fault";
      detail = "Waiting for Azure to confirm the CPU workers have stopped.";
    } else if (this.incidentPhase === "reset") {
      title = "CPU reset confirmed; verify recovery";
      detail =
        "The CPU reset completed without changing the PostgreSQL fault. " +
        "Allow new CPU samples to arrive, refresh the Orders GUI, and check " +
        "that the alert resolves as described in Task 6.";
    } else if (this.incidentPhase.endsWith("-error")) {
      title = this.incidentPhase === "reset-error"
        ? "CPU reset not confirmed"
        : "CPU start not confirmed";
      detail =
        "Read the error above. A timed-out request may still have changed the VM. " +
        "Check the graph and Orders GUI, then reset the CPU incident before retrying.";
    }

    if (this.progressTitle.textContent !== title) {
      this.progressTitle.textContent = title;
    }
    this.progressDetail.textContent = detail;
    setHidden(
      this.progressSteps,
      !["starting", "running"].includes(this.incidentPhase),
    );
    this.steps.forEach((step, index) => {
      const state = index < currentStep ? "complete" : index === currentStep ? "current" : "pending";
      step.dataset.state = state;
      step.setAttribute("aria-current", index === currentStep ? "step" : "false");
      step.setAttribute("aria-label", `${step.textContent}: ${state}`);
    });
  }

  renderRefreshStatus(now = Date.now()) {
    const state = !this.app.connected
      ? "offline"
      : this.refreshing
        ? "loading"
        : this.telemetryError
          ? "error"
          : "live";
    this.live.dataset.state = state;
    this.live.textContent = {
      offline: "Offline",
      loading: "Updating",
      error: "Retry scheduled",
      live: "Live",
    }[state];
    this.refresh.textContent = this.refreshing ? "Refreshing..." : "Refresh now";
    this.refreshCountdown.textContent =
      !this.app.connected || this.refreshing || this.nextRefreshAt === null
        ? ""
        : `Next in ${Math.max(0, Math.ceil((this.nextRefreshAt - now) / 1000))}s`;
  }

  renderGraph() {
    renderLineChart(
      this.graph,
      this.points,
      this.scenario,
      new Date(),
      this.incidentRequestedAt,
    );
  }

  async refreshGraph() {
    if (this.refreshing || !this.app.connected) {
      return;
    }
    const version = ++this.refreshVersion;
    this.refreshing = true;
    this.refresh.disabled = true;
    this.graph.setAttribute("aria-busy", "true");
    this.renderRefreshStatus();
    this.graphStatus.dataset.kind = "";
    this.graphStatus.textContent = "Refreshing telemetry...";
    try {
      const points = await this.app.chartSeries(this.scenario.id);
      if (version !== this.refreshVersion) {
        return;
      }
      this.points = points;
      this.telemetryError = false;
      this.renderGraph();
      this.renderCpuProgress();
      const latest = points[points.length - 1];
      this.graphStatus.textContent =
        `Updated ${timeLabel(new Date(), true)} | ` +
        (latest ? `Latest sample ${timeLabel(latest.timestamp)}` : "No samples yet");
    } catch (error) {
      if (version !== this.refreshVersion) {
        return;
      }
      this.telemetryError = true;
      this.graphStatus.dataset.kind = "error";
      this.graphStatus.textContent = `Telemetry error: ${this.app.errorMessage(error)}`;
      this.app.reportError(error);
    } finally {
      if (version === this.refreshVersion) {
        this.refreshing = false;
        this.graph.setAttribute("aria-busy", "false");
        this.nextRefreshAt = Date.now() + this.refreshSeconds * 1000;
        this.refresh.disabled = !this.app.connected;
        this.renderRefreshStatus();
      }
    }
  }
}
