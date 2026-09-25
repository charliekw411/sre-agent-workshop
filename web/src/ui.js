import { SCENARIOS, formatPercent } from "./core.js";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";

function element(tag, className = "", text = "") {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text) {
    node.textContent = text;
  }
  return node;
}

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
    this.retry = actionButton("Retry connection", "md-button md-button--primary");
    this.change = actionButton("Change environment", "md-button");
    this.disconnect = actionButton("Disconnect", "md-button");
    this.actionsRoot.append(this.retry, this.change, this.disconnect);
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

function timeLabel(date) {
  return new Intl.DateTimeFormat([], {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(date);
}

export function renderLineChart(root, points, scenario, now = new Date()) {
  root.replaceChildren();
  if (!points.length) {
    root.append(
      element(
        "p",
        "sre-chart__empty",
        "No samples are available yet. Azure Monitor ingestion can take several minutes.",
      ),
    );
    return;
  }

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
  if (!visible.length) {
    root.append(
      element("p", "sre-chart__empty", "Azure returned no samples in the last 30 minutes."),
    );
    return;
  }

  const values = visible.map((point) => point.value);
  const latest = visible[visible.length - 1];
  const summary = element("p", "sre-chart__summary");
  summary.textContent =
    scenario.id === "cpu"
      ? `Latest ${formatPercent(latest.value)} | Peak ${formatPercent(Math.max(...values))}`
      : `Latest ${formatPercent(latest.value)} free | Minimum ${formatPercent(
          Math.min(...values),
        )} free`;
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
    return "Incident reset completed. Workshop ballast was removed and fault units are inactive.";
  }
  if (action === "status") {
    return `Fault status: CPU ${result.cpu || "unknown"}, disk ${result.disk || "unknown"}.`;
  }
  if (action === "cpu") {
    return `CPU incident started for ${result.seconds || 600} seconds with ${
      result.workers || 2
    } workers.`;
  }
  return `Disk incident started at ${result.targetPercent || 90}% used for ${
    result.seconds || 600
  } seconds.`;
}

export class IncidentLauncher {
  constructor(root, app, scenarioId) {
    this.root = root;
    this.app = app;
    this.scenario = SCENARIOS[scenarioId];
    this.environmentKey = "";
    this.refreshing = false;
    this.timer = null;
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
      this.scenario.id === "cpu"
        ? "Starts the existing bounded 10-minute, two-worker CPU fault through VM Run Command."
        : "Creates the existing 10-minute, 90%-used ballast on /var/lib/orders through VM Run Command.",
    );
    const actions = element("div", "sre-launcher__actions");
    this.run = actionButton(this.scenario.runLabel, "md-button md-button--primary");
    this.reset = actionButton("Reset incident", "md-button");
    this.statusButton = actionButton("Check fault status", "md-button");
    this.refresh = actionButton("Refresh graph", "md-button");
    actions.append(this.run, this.reset, this.statusButton, this.refresh);

    this.status = element("p", "sre-launcher__status");
    this.status.setAttribute("aria-live", "polite");
    this.status.textContent = "Connect to Azure from the site header to enable controls.";

    const graphHeader = element("div", "sre-chart__header");
    const graphTitle = element("h4", "", `${this.scenario.chartTitle} | Last 30 minutes`);
    this.graphStatus = element(
      "span",
      "sre-chart__refresh",
      `Automatic refresh every ${this.app.config.refreshSeconds} seconds`,
    );
    graphHeader.append(graphTitle, this.graphStatus);
    this.graph = element("div", "sre-chart");
    this.graph.append(
      element("p", "sre-chart__empty", "Connect to Azure to load telemetry."),
    );

    this.links = element("nav", "sre-launcher__links");
    this.links.setAttribute("aria-label", "Incident investigation links");
    this.chartLink = element(
      "a",
      "",
      this.scenario.id === "cpu"
        ? "Open Azure Monitor chart"
        : "Open Log Analytics workspace",
    );
    this.alertLink = element("a", "", "Open Azure Monitor alert");
    this.agentLink = element("a", "", "Open SRE Agent incidents");
    for (const link of [this.chartLink, this.alertLink, this.agentLink]) {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    }
    this.links.append(this.chartLink, this.alertLink, this.agentLink);
    setHidden(this.links, true);

    const safety = element(
      "p",
      "sre-launcher__safety",
      this.scenario.id === "cpu"
        ? "Fixed limits: 600 seconds, 2 workers, 256 MiB memory cap, reduced scheduling priority."
        : "Fixed limits: 90% used, 600 seconds, 128 MiB recovery reserve; reset removes only .workshop-disk-pressure.",
    );
    this.root.append(
      heading,
      description,
      actions,
      this.status,
      graphHeader,
      this.graph,
      this.links,
      safety,
    );

    this.run.addEventListener("click", () => this.execute(this.scenario.action));
    this.reset.addEventListener("click", () => this.execute("reset"));
    this.statusButton.addEventListener("click", () => this.execute("status"));
    this.refresh.addEventListener("click", () => this.refreshGraph(true));
  }

  async execute(action) {
    this.status.dataset.kind = "progress";
    this.status.textContent =
      action === "reset"
        ? "Resetting the workshop fault through Azure VM Run Command..."
        : action === "status"
          ? "Checking fault status through Azure VM Run Command..."
          : "Starting the bounded workshop fault through Azure VM Run Command...";
    try {
      const result = await this.app.executeFault(action);
      this.status.dataset.kind = "success";
      this.status.textContent = describeResult(action, result);
      await this.refreshGraph(false);
    } catch (error) {
      this.status.dataset.kind = "error";
      this.status.textContent = this.app.errorMessage(error);
      this.app.reportError(error);
    }
  }

  update(state) {
    const connected = state.phase === "connected" && state.environment;
    const busy = Boolean(state.operation);
    for (const button of [this.reset, this.statusButton, this.refresh]) {
      button.disabled = !connected || busy;
    }
    this.run.disabled = !connected || busy || state.requiresStatusCheck;

    if (!connected) {
      this.stopTimer();
      this.environmentKey = "";
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

    if (busy) {
      this.status.dataset.kind = "progress";
      this.status.textContent = "An authenticated VM Run Command operation is in progress.";
    }
    const key = `${state.environment.subscription.id}:${state.environment.resourceGroup.id}`;
    if (key !== this.environmentKey) {
      this.environmentKey = key;
      this.status.dataset.kind = "success";
      this.status.textContent = `Targeting ${state.environment.resourceGroup.name} / ${state.environment.virtualMachine.name}.`;
      const links = this.app.incidentLinks(this.scenario.id);
      this.chartLink.href = links.chart;
      this.alertLink.href = links.alert;
      this.agentLink.href = links.investigation || "#";
      setHidden(this.agentLink, !links.investigation);
      setHidden(this.links, false);
      void this.refreshGraph(false);
    }
    this.startTimer();
  }

  startTimer() {
    if (this.timer) {
      return;
    }
    this.timer = window.setInterval(() => {
      if (document.visibilityState === "visible" && this.root.isConnected) {
        void this.refreshGraph(false);
      }
    }, this.app.config.refreshSeconds * 1000);
  }

  stopTimer() {
    if (this.timer) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
  }

  async refreshGraph(manual) {
    if (this.refreshing || !this.app.connected) {
      return;
    }
    this.refreshing = true;
    this.refresh.disabled = true;
    this.graphStatus.textContent = "Refreshing telemetry...";
    try {
      const points = await this.app.chartSeries(this.scenario.id);
      renderLineChart(this.graph, points, this.scenario);
      this.graphStatus.textContent = `Updated ${timeLabel(new Date())} | automatic refresh every ${this.app.config.refreshSeconds} seconds`;
    } catch (error) {
      this.graphStatus.textContent = `Telemetry error: ${this.app.errorMessage(error)}`;
      if (manual) {
        this.status.dataset.kind = "error";
        this.status.textContent = this.app.errorMessage(error);
      }
      this.app.reportError(error);
    } finally {
      this.refreshing = false;
      this.refresh.disabled = !this.app.connected || Boolean(this.app.state.operation);
    }
  }
}
