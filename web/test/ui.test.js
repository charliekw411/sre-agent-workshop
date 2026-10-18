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
    this.listeners = new Map();
    this.isConnected = true;
    this.classList = {
      add: (className) => {
        this.className = `${this.className} ${className}`.trim();
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

  addEventListener(name, listener) {
    this.listeners.set(name, listener);
  }

  click() {
    if (!this.disabled) {
      return this.listeners.get("click")?.();
    }
  }

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
    root.className?.split(" ").includes(className) ||
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

test("chart marks a fault request without claiming Azure confirmed its start", () => {
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

  assert.match(findByClass(root, "sre-chart__summary").textContent, /Fault requested/);
  assert.match(
    findByClass(root, "sre-chart__summary").textContent,
    /Waiting for Azure Monitor samples/,
  );
  assert.ok(findByClass(root, "sre-chart__event"));
  assert.equal(
    findByClass(root, "sre-chart__event-label").textContent,
    "Fault requested",
  );
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

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}

async function settle() {
  await new Promise((resolve) => setImmediate(resolve));
}

async function launcherFixture(
  t,
  { scenario = "cpu", series = async () => [], fault = async () => ({}) } = {},
) {
  const timers = new Map();
  let timerId = 0;
  globalThis.document = {
    createElement: (tag) => new FakeNode(tag),
    createElementNS: (_namespace, tag) => new FakeNode(tag),
    visibilityState: "visible",
  };
  globalThis.window = {
    setInterval: (callback, interval) => {
      timers.set(++timerId, { callback, interval });
      return timerId;
    },
    clearInterval: (id) => timers.delete(id),
  };
  const environment = {
    subscription: { id: "workshop-subscription" },
    resourceGroup: { id: "workshop-resource-group", name: "rg-demo" },
    virtualMachine: { name: "vm-orders-demo" },
    postgresqlServer: { name: "psql-orders-demo" },
  };
  const app = {
    config: { refreshSeconds: 60 },
    state: {
      phase: "connected",
      environment,
      operation: null,
      requiresStatusCheck: false,
    },
    get connected() {
      return this.state.phase === "connected" && Boolean(this.state.environment);
    },
    seriesCalls: 0,
    faultCalls: [],
    errors: [],
    chartSeries: async (id) => {
      app.seriesCalls += 1;
      return series(id, app.seriesCalls);
    },
    errorMessage: (error) => error.message,
    reportError: (error) => app.errors.push(error),
    incidentLinks: () => ({ investigation: "https://example.test/incidents" }),
    executeFault: async (action) => {
      app.faultCalls.push(action);
      app.state.operation = { action };
      launcher.update(app.state);
      try {
        return await fault(action);
      } finally {
        app.state.operation = null;
        launcher.update(app.state);
      }
    },
  };
  const launcher = new IncidentLauncher(new FakeNode("div"), app, scenario);
  launcher.update(app.state);
  await settle();
  t.after(() => launcher.stopTimer());
  return { launcher, app, timers };
}

test("pending CPU launch immediately shows progress, a request marker, and elapsed time", async (t) => {
  const pending = deferred();
  const { launcher, app } = await launcherFixture(t, {
    fault: () => pending.promise,
  });
  const operation = launcher.execute("cpu");

  assert.equal(launcher.progress.hidden, false);
  assert.equal(launcher.progressTitle.textContent, "Starting CPU fault");
  assert.match(launcher.status.textContent, /Azure VM Run Command/);
  assert.equal(launcher.run.textContent, "Starting CPU incident...");
  assert.equal(launcher.run.disabled, true);
  assert.equal(launcher.run.attributes["aria-busy"], "true");
  assert.equal(launcher.refresh.disabled, false);
  assert.match(findByClass(launcher.graph, "sre-chart__summary").textContent, /Fault requested/);
  launcher.renderCpuProgress(new Date(launcher.incidentRequestedAt.getTime() + 180000));
  assert.equal(launcher.elapsed.textContent, "Elapsed 3:00");
  assert.match(launcher.progressDetail.textContent, /may already be running/);
  assert.equal(launcher.steps[0].attributes["aria-current"], "step");

  launcher.run.click();
  assert.deepEqual(app.faultCalls, ["cpu"]);
  pending.resolve({ seconds: 600, workers: 2 });
  await operation;
  assert.equal(launcher.run.attributes["aria-busy"], "false");
});

test("manual refresh remains available during CPU start without resubmitting the fault", async (t) => {
  const pending = deferred();
  const { launcher, app } = await launcherFixture(t, {
    fault: () => pending.promise,
  });
  const operation = launcher.execute("cpu");
  await launcher.refresh.click();

  assert.equal(app.seriesCalls, 2);
  assert.deepEqual(app.faultCalls, ["cpu"]);
  assert.equal(launcher.refresh.disabled, false);
  assert.equal(launcher.live.textContent, "Live");
  assert.match(launcher.graphStatus.textContent, /Updated.*No samples yet/);
  pending.resolve({ seconds: 600, workers: 2 });
  await operation;
});

test("CPU confirmation advances to telemetry without claiming an alert has fired", async (t) => {
  const { launcher } = await launcherFixture(t);
  await launcher.execute("cpu");

  assert.match(launcher.progressTitle.textContent, /CPU fault confirmed; waiting for telemetry/);
  assert.equal(launcher.steps[0].dataset.state, "complete");
  assert.equal(launcher.steps[1].dataset.state, "current");
  assert.equal(launcher.steps[2].dataset.state, "pending");
  assert.match(launcher.progressDetail.textContent, /cannot speed up Azure Monitor ingestion/);
  assert.match(launcher.status.textContent, /CPU incident started for 600 seconds/);
});

test("only post-request CPU samples greater than 80 advance to checking the alert", async (t) => {
  const { launcher } = await launcherFixture(t);
  await launcher.execute("cpu");
  const requested = launcher.incidentRequestedAt.getTime();
  const now = new Date(requested + 180000);

  for (const points of [
    [{ timestamp: new Date(requested - 60000), value: 99 }],
    [{ timestamp: new Date(requested + 60000), value: 80 }],
    [{ timestamp: new Date(requested + 240000), value: 99 }],
  ]) {
    launcher.points = points;
    launcher.renderCpuProgress(now);
    assert.equal(launcher.steps[2].dataset.state, "pending");
  }

  launcher.points = [{ timestamp: new Date(requested + 60000), value: 80.1 }];
  launcher.renderCpuProgress(now);
  assert.equal(launcher.progressTitle.textContent, "High CPU observed; check alert history");
  assert.equal(launcher.steps[1].dataset.state, "complete");
  assert.equal(launcher.steps[2].dataset.state, "current");
  assert.match(launcher.progressDetail.textContent, /five-minute average every minute/);
  assert.match(launcher.progressDetail.textContent, /does not confirm it fired/);
  assert.match(launcher.progressDetail.textContent, /5 to 10 minutes/);
});

test("late or falling CPU samples give next steps without claiming verified recovery", async (t) => {
  const { launcher } = await launcherFixture(t);
  await launcher.execute("cpu");
  const requested = launcher.incidentRequestedAt.getTime();
  const now = new Date(requested + 360000);

  launcher.renderCpuProgress(now);
  assert.match(launcher.progressDetail.textContent, /ask your facilitator before retrying/);

  launcher.points = [
    { timestamp: new Date(requested + 60000), value: 98 },
    { timestamp: new Date(requested + 300000), value: 12 },
  ];
  launcher.renderCpuProgress(now);
  assert.equal(launcher.progressTitle.textContent, "CPU sample below threshold; check recovery");
  assert.match(launcher.progressDetail.textContent, /verify recovery in Task 6/);
  assert.equal(launcher.steps[2].dataset.state, "current");
});

test("CPU reset replaces launch guidance with recovery checks", async (t) => {
  const { launcher, app } = await launcherFixture(t);
  await launcher.execute("cpu");
  await launcher.execute("reset-cpu");

  assert.equal(launcher.progressTitle.textContent, "CPU reset confirmed; verify recovery");
  assert.equal(launcher.progressSteps.hidden, true);
  assert.match(launcher.status.textContent, /without changing the PostgreSQL fault/);
  assert.match(launcher.progressDetail.textContent, /Orders GUI/);
  assert.deepEqual(app.faultCalls, ["cpu", "reset-cpu"]);
});

test("ambiguous CPU start stays unconfirmed and preserves the retry safety lock", async (t) => {
  const pending = deferred();
  const { launcher, app } = await launcherFixture(t, {
    fault: () => pending.promise,
  });
  const operation = launcher.execute("cpu");
  app.state.requiresStatusCheck = true;
  pending.reject(new Error("Run Command timed out; the fault may still have started."));
  await operation;

  assert.equal(launcher.status.dataset.kind, "error");
  assert.match(launcher.status.textContent, /timed out/);
  assert.equal(launcher.progressTitle.textContent, "CPU start not confirmed");
  assert.equal(launcher.progressSteps.hidden, true);
  assert.match(launcher.progressDetail.textContent, /reset the CPU incident before retrying/);
  assert.equal(launcher.run.disabled, true);
  assert.equal(launcher.reset.disabled, false);
  assert.equal(launcher.refresh.disabled, false);
  assert.equal(app.errors.length, 1);
});

test("a failed CPU reset never reports success", async (t) => {
  const { launcher, app } = await launcherFixture(t, {
    fault: async (action) => {
      if (action === "reset-cpu") {
        throw new Error("CPU reset could not be verified.");
      }
      return {};
    },
  });
  await launcher.execute("cpu");
  await launcher.execute("reset-cpu");

  assert.equal(launcher.status.dataset.kind, "error");
  assert.equal(launcher.progressTitle.textContent, "CPU reset not confirmed");
  assert.equal(app.errors.length, 1);
});

test("telemetry refresh prevents overlapping reads even when the operation state changes", async (t) => {
  const pending = deferred();
  const { launcher, app } = await launcherFixture(t, {
    series: (_id, call) => call === 1 ? [] : pending.promise,
  });
  const refresh = launcher.refreshGraph();
  app.state.operation = { action: "cpu" };
  launcher.update(app.state);
  await launcher.refreshGraph();

  assert.equal(app.seriesCalls, 2);
  assert.equal(launcher.refresh.disabled, true);
  assert.equal(launcher.refresh.textContent, "Refreshing...");
  assert.equal(launcher.live.textContent, "Updating");
  assert.equal(launcher.graph.attributes["aria-busy"], "true");
  pending.resolve([]);
  await refresh;
  assert.equal(launcher.refresh.disabled, false);
  assert.equal(launcher.graph.attributes["aria-busy"], "false");
});

test("CPU telemetry polls every 15 seconds while the countdown ticks each second", async (t) => {
  const { launcher, app, timers } = await launcherFixture(t);
  const timer = timers.get(launcher.timer);
  assert.equal(timer.interval, 1000);
  const nextRefresh = launcher.nextRefreshAt;
  launcher.renderRefreshStatus(nextRefresh - 10000);
  assert.equal(launcher.refreshCountdown.textContent, "Next in 10s");
  assert.equal(launcher.refreshCountdown.attributes["aria-live"], "off");

  timer.callback();
  await settle();
  assert.equal(app.seriesCalls, 1);
  launcher.nextRefreshAt = 0;
  document.visibilityState = "hidden";
  timer.callback();
  await settle();
  assert.equal(app.seriesCalls, 1);
  document.visibilityState = "visible";
  timer.callback();
  await settle();
  assert.equal(app.seriesCalls, 2);
  assert.match(launcher.refreshCountdown.textContent, /Next in 15s/);

  launcher.stopTimer();
  assert.equal(timers.size, 0);
});

test("telemetry errors are visible, reported, and retried without losing fault confirmation", async (t) => {
  const { launcher, app } = await launcherFixture(t, {
    series: async (_id, call) => {
      if (call === 2 || call === 3) {
        throw new Error("Azure Monitor is temporarily unavailable.");
      }
      return [];
    },
  });
  await launcher.execute("cpu");

  assert.equal(launcher.live.textContent, "Retry scheduled");
  assert.equal(launcher.graphStatus.dataset.kind, "error");
  assert.match(launcher.graphStatus.textContent, /Telemetry error: Azure Monitor/);
  assert.equal(launcher.status.dataset.kind, "success");
  assert.equal(launcher.incidentPhase, "running");
  assert.equal(launcher.refresh.disabled, false);
  assert.ok(launcher.nextRefreshAt > Date.now());
  await launcher.refreshGraph();
  assert.equal(launcher.status.dataset.kind, "success");
  assert.equal(app.errors.length, 2);
  await launcher.refreshGraph();
  assert.equal(launcher.live.textContent, "Live");
  assert.equal(launcher.graphStatus.dataset.kind, "");
});

test("disconnect clears progress and ignores an in-flight telemetry response", async (t) => {
  const pending = deferred();
  const { launcher, app, timers } = await launcherFixture(t, {
    series: (_id, call) => call === 1 ? [] : pending.promise,
  });
  const operation = launcher.execute("cpu");
  await settle();
  app.state.phase = "not-connected";
  app.state.environment = null;
  launcher.update(app.state);
  pending.resolve([{ timestamp: new Date(), value: 99 }]);
  await operation;

  assert.deepEqual(launcher.points, []);
  assert.equal(launcher.progress.hidden, true);
  assert.equal(launcher.live.textContent, "Offline");
  assert.equal(launcher.refreshCountdown.textContent, "");
  assert.equal(launcher.refresh.disabled, true);
  assert.equal(timers.size, 0);
  assert.match(launcher.graphStatus.textContent, /Connect to Azure/);
});

test("changing environments ignores stale telemetry errors and fault results", async (t) => {
  const staleSeries = deferred();
  const staleFault = deferred();
  const { launcher, app } = await launcherFixture(t, {
    series: (_id, call) => call === 2 ? staleSeries.promise : [],
    fault: () => staleFault.promise,
  });
  const refresh = launcher.refreshGraph();
  const operation = launcher.execute("cpu");
  app.state.environment = {
    ...app.state.environment,
    resourceGroup: { id: "another-resource-group", name: "rg-other" },
  };
  launcher.update(app.state);
  await settle();
  staleSeries.reject(new Error("Old environment permission error."));
  staleFault.resolve({ seconds: 600, workers: 2 });
  await Promise.all([refresh, operation]);

  assert.deepEqual(launcher.points, []);
  assert.equal(launcher.progress.hidden, true);
  assert.equal(launcher.incidentRequestedAt, null);
  assert.equal(app.errors.length, 0);
  assert.doesNotMatch(launcher.status.textContent, /CPU incident started/);
  assert.equal(launcher.live.textContent, "Live");
});

test("PostgreSQL controls retain scenario-specific actions without CPU-only guidance", async (t) => {
  const { launcher, app } = await launcherFixture(t, { scenario: "postgresql" });
  await launcher.execute("postgresql");
  assert.equal(launcher.progress.hidden, true);
  assert.match(launcher.status.textContent, /scoped outbound NSG rule/);
  assert.equal(launcher.run.textContent, SCENARIOS.postgresql.runLabel);
  await launcher.execute("reset-postgresql");
  assert.match(launcher.status.textContent, /CPU fault was not changed/);
  assert.deepEqual(app.faultCalls, ["postgresql", "reset-postgresql"]);
  assert.equal(launcher.refresh.disabled, false);
  assert.equal(launcher.refreshSeconds, 60);
});
