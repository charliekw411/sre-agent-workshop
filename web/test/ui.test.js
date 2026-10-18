import test from "node:test";
import assert from "node:assert/strict";

import { SCENARIOS } from "../src/core.js";
import { HeaderConnection, IncidentLauncher, renderLineChart } from "../src/ui.js";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.dataset = {};
    this.classList = {
      add: (className) => {
        this.className = [this.className, className].filter(Boolean).join(" ");
      },
    };
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = children;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener() {}

  insertBefore(child) {
    this.children.push(child);
  }

  querySelector(selector) {
    return selector.startsWith(".")
      ? findByClass(this, selector.slice(1))
      : null;
  }
}

function findByClass(root, className) {
  if (
    root.className === className ||
    root.attributes?.class === className
  ) {
    return root;
  }
  for (const child of root.children || []) {
    const match = findByClass(child, className);
    if (match) {
      return match;
    }
  }
  return null;
}

async function launcherFixture(t, scenarioId = "postgresql") {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const intervals = new Map();
  let nextTimer = 0;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createElementNS: (_namespace, tag) => new FakeNode(tag),
    visibilityState: "visible",
  };
  globalThis.window = {
    setInterval: (callback, milliseconds) => {
      const id = ++nextTimer;
      intervals.set(id, { callback, milliseconds });
      return id;
    },
    clearInterval: (id) => intervals.delete(id),
  };
  const environment = {
    subscription: { id: "33333333-3333-4333-8333-333333333333" },
    resourceGroup: { id: "/resourceGroups/rg-01", name: "rg-01" },
    virtualMachine: { name: "vm-orders-demo" },
    postgresqlServer: { name: "psql-orders-demo" },
    ordersGuiUrl: "http://orders-demo.australiaeast.cloudapp.azure.com:8080/",
  };
  const reportedErrors = [];
  const app = {
    config: { refreshSeconds: 60 },
    connected: true,
    state: {
      phase: "connected",
      environment,
      operation: null,
      requiresStatusCheck: false,
    },
    executeFault: async () => ({ ok: true }),
    chartSeries: async () => [],
    incidentLinks: () => ({
      alert: "https://portal.azure.com/#alert-orders-postgresql-connectivity",
      investigation: "https://sre.azure.com/incidents",
    }),
    errorMessage: (error) => error.message,
    reportError: (error) => reportedErrors.push(error),
  };
  const root = new FakeNode("div");
  root.isConnected = true;
  const launcher = new IncidentLauncher(root, app, scenarioId);
  t.after(() => {
    launcher.stopTimer();
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  });
  launcher.update(app.state);
  await Promise.resolve();
  return { launcher, app, root, intervals, reportedErrors };
}

test("chart shows an incident marker while Azure Monitor samples are pending", () => {
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createElementNS: (_namespace, tag) => new FakeNode(tag),
  };
  const root = new FakeNode("div");
  const started = new Date("2026-10-06T23:01:00Z");

  renderLineChart(
    root,
    [],
    SCENARIOS.cpu,
    new Date("2026-10-06T23:02:00Z"),
    started,
  );

  assert.match(findByClass(root, "sre-chart__summary").textContent, /Incident started/);
  assert.match(
    findByClass(root, "sre-chart__summary").textContent,
    /Waiting for Azure Monitor samples/,
  );
  assert.ok(findByClass(root, "sre-chart__event"));
  assert.equal(
    findByClass(root, "sre-chart__event-label").textContent,
    "Incident started",
  );
});

test("PostgreSQL request marker is distinct from confirmed injection or alert status", () => {
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createElementNS: (_namespace, tag) => new FakeNode(tag),
  };
  const root = new FakeNode("div");
  const requested = new Date("2026-10-06T23:01:00Z");

  renderLineChart(
    root,
    [{ timestamp: new Date("2026-10-06T23:02:00Z"), value: 100 }],
    SCENARIOS.postgresql,
    new Date("2026-10-06T23:03:00Z"),
    requested,
    "Fault requested",
  );

  const summary = findByClass(root, "sre-chart__summary").textContent;
  assert.match(summary, /Fault requested/);
  assert.match(summary, /Sample time \d{2}:\d{2}/);
  assert.equal(findByClass(root, "sre-chart__event-label").textContent, "Fault requested");
  assert.doesNotMatch(summary, /Fault confirmed|alert fired/i);
});

test("PostgreSQL launcher shows live progress and three minutes elapsed before confirmation", async (t) => {
  const { launcher, app, root, intervals } = await launcherFixture(t);
  let complete;
  const operation = new Promise((resolve) => {
    complete = resolve;
  });
  app.executeFault = async (action, onProgress) => {
    assert.equal(action, "postgresql");
    app.state.operation = { action };
    launcher.update(app.state);
    onProgress("Step 2 of 3: Setting PostgreSqlFaultInjection to Deny and verifying the rule.");
    launcher.update(app.state);
    const result = await operation;
    app.state.operation = null;
    launcher.update(app.state);
    return result;
  };

  const execution = launcher.execute("postgresql");
  assert.equal(launcher.status.dataset.kind, "progress");
  assert.match(launcher.status.textContent, /^Step 2 of 3:/);
  assert.doesNotMatch(launcher.status.textContent, /Fault confirmed/);
  assert.equal(launcher.progress.hidden, false);
  assert.equal(launcher.run.disabled, true);
  assert.equal(launcher.reset.disabled, true);
  assert.equal(launcher.refresh.disabled, true);
  assert.equal(findByClass(root, "sre-chart__event-label").textContent, "Fault requested");
  assert.match(findByClass(root, "sre-chart__summary").textContent, /Waiting for Azure Monitor/);

  t.mock.method(Date, "now", () => launcher.incidentStartedAt.getTime() + 180000);
  intervals.get(launcher.progressTimer).callback();
  assert.match(launcher.elapsed.textContent, /Elapsed 03:00/);
  assert.match(launcher.elapsed.textContent, /Do not repeat/);
  assert.equal(launcher.elapsed.attributes["aria-live"], "off");

  complete({ ok: true, postgresqlAccess: "Deny", postgresqlConnectivity: "unavailable" });
  await execution;
  assert.equal(launcher.status.dataset.kind, "success");
  assert.match(launcher.status.textContent, /^Fault confirmed:/);
  assert.match(launcher.status.textContent, /Continue to Task 4/);
  assert.match(launcher.status.textContent, /automatic diagnosis and repair/);
  assert.equal(launcher.progress.hidden, true);
  assert.equal(launcher.progressTimer, null);
  assert.equal(launcher.run.disabled, false);
  assert.equal(intervals.size, 1);
});

test("PostgreSQL launcher exposes environment links and explains graph versus alert status", async (t) => {
  const { launcher, app, root } = await launcherFixture(t);
  assert.equal(launcher.ordersLink.href, app.state.environment.ordersGuiUrl);
  assert.equal(
    launcher.alertLink.href,
    "https://portal.azure.com/#alert-orders-postgresql-connectivity",
  );
  assert.equal(launcher.agentLink.href, "https://sre.azure.com/incidents");
  for (const link of [launcher.ordersLink, launcher.alertLink, launcher.agentLink]) {
    assert.equal(link.hidden, false);
    assert.equal(link.target, "_blank");
    assert.equal(link.rel, "noopener noreferrer");
  }
  assert.match(findByClass(root, "sre-launcher__safety").textContent, /not that an alert has fired/);
  assert.match(findByClass(root, "sre-launcher__safety").textContent, /Older failures remain/);
  assert.equal(launcher.status.attributes.role, "status");
});

test("a failed PostgreSQL operation stops progress without claiming fault confirmation", async (t) => {
  const { launcher, root, intervals, app, reportedErrors } = await launcherFixture(t);
  const error = new Error("The rule update was interrupted; reset before retrying.");
  app.executeFault = async (_action, onProgress) => {
    onProgress("Step 2 of 3: Setting PostgreSqlFaultInjection to Deny and verifying the rule.");
    throw error;
  };

  await launcher.execute("postgresql");

  assert.equal(launcher.status.dataset.kind, "error");
  assert.equal(launcher.status.textContent, error.message);
  assert.equal(launcher.progress.hidden, true);
  assert.equal(launcher.progressTimer, null);
  assert.equal(launcher.executingAction, null);
  assert.equal(intervals.size, 1);
  assert.deepEqual(reportedErrors, [error]);
  assert.equal(findByClass(root, "sre-chart__event-label").textContent, "Fault requested");
});

test("leaving an incident launcher clears graph and progress timers", async (t) => {
  const { launcher, intervals } = await launcherFixture(t);
  launcher.startProgressTimer(new Date());
  assert.equal(intervals.size, 2);

  launcher.stopTimer();

  assert.equal(intervals.size, 0);
  assert.equal(launcher.timer, null);
  assert.equal(launcher.progressTimer, null);
  assert.equal(launcher.progress.hidden, true);
});

test("disconnecting hides investigation links and stops launcher progress", async (t) => {
  const { launcher, app, intervals } = await launcherFixture(t);
  launcher.startProgressTimer(new Date());
  launcher.update({ ...app.state, phase: "not-connected", environment: null });

  assert.equal(launcher.links.hidden, true);
  assert.equal(launcher.run.disabled, true);
  assert.equal(launcher.reset.disabled, true);
  assert.equal(launcher.refresh.disabled, true);
  assert.equal(launcher.progress.hidden, true);
  assert.equal(intervals.size, 0);
});

test("PostgreSQL reset keeps its recovery message without marking a new fault request", async (t) => {
  const { launcher, root } = await launcherFixture(t);
  await launcher.execute("reset-postgresql");

  assert.match(launcher.status.textContent, /PostgreSQL incident reset completed/);
  assert.match(launcher.status.textContent, /CPU fault was not changed/);
  assert.equal(findByClass(root, "sre-chart__event-label"), null);
  assert.equal(launcher.progress.hidden, true);
});

test("CPU start and reset retain their scenario-specific behavior", async (t) => {
  const { launcher, root } = await launcherFixture(t, "cpu");
  assert.equal(findByClass(root, "sre-launcher__description").hidden, true);
  assert.equal(findByClass(root, "sre-launcher__safety").hidden, true);

  await launcher.execute("cpu");
  assert.match(launcher.status.textContent, /CPU incident started for 600 seconds with 2 workers/);
  assert.equal(findByClass(root, "sre-chart__event-label").textContent, "Incident started");

  await launcher.execute("reset-cpu");
  assert.match(launcher.status.textContent, /CPU incident reset completed without changing/);
  assert.equal(launcher.progress.hidden, true);
});

test("connected panel exposes only the selected environment Orders GUI", () => {
  const header = new FakeNode("header");
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    querySelector: (selector) =>
      selector === ".md-header__inner" ? header : null,
    addEventListener: () => {},
  };
  const connection = new HeaderConnection({
    connect: () => {},
    retry: () => {},
    changeEnvironment: () => {},
    disconnect: () => {},
  });
  assert.equal(connection.mount(), true);

  connection.render({
    phase: "connected",
    configuredTenant: "11111111-1111-4111-8111-111111111111",
    account: { name: "Participant 1" },
    environment: {
      subscription: {
        id: "33333333-3333-4333-8333-333333333333",
        name: "Workshop",
      },
      resourceGroup: { name: "rg-01" },
      ordersGuiUrl:
        "http://orders-demo.australiaeast.cloudapp.azure.com:8080/",
    },
  });
  assert.equal(connection.ordersGui.hidden, false);
  assert.equal(
    connection.ordersGui.href,
    "http://orders-demo.australiaeast.cloudapp.azure.com:8080/",
  );

  connection.render({
    phase: "not-connected",
    configuredTenant: "11111111-1111-4111-8111-111111111111",
    account: null,
    environment: null,
  });
  assert.equal(connection.ordersGui.hidden, true);
  assert.equal(connection.ordersGui.href, "#");
});
