import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  buildCpuMetricsUrl,
  buildDiskQuery,
  createFaultScript,
  isWorkshopResourceGroup,
  parseLogSeries,
  parseMetricSeries,
  parseRunCommandResult,
  selectEnvironmentResources,
  sreAgentIncidentsUrl,
  validateConfig,
} from "../src/core.js";

const TENANT = "11111111-1111-4111-8111-111111111111";
const CLIENT = "22222222-2222-4222-8222-222222222222";
const REQUEST = "a".repeat(32);
const VM_ID =
  "/subscriptions/33333333-3333-4333-8333-333333333333/resourceGroups/" +
  "rg-sre-agent-workshop-demo/providers/Microsoft.Compute/virtualMachines/vm-orders-demo";

test("site configuration requires a tenant-bound SPA", () => {
  const unconfigured = validateConfig({ tenantId: "", clientId: "" });
  assert.equal(unconfigured.configured, false);
  assert.equal(unconfigured.refreshSeconds, 60);
  assert.equal(
    validateConfig({
      tenantId: TENANT,
      clientId: CLIENT,
      redirectUri: "https://example.github.io/workshop/",
    }).configured,
    true,
  );
  assert.equal(
    validateConfig({
      tenantId: TENANT,
      clientId: CLIENT,
      redirectUri: "http://example.test/workshop/",
    }).configured,
    false,
  );
});

test("environment discovery accepts only tagged workshop resource groups", () => {
  const config = {
    resourceGroupPrefix: "rg-sre-agent-workshop-",
    environmentTagName: "workshop-architecture",
    environmentTagValue: "single-vm",
  };
  assert.equal(
    isWorkshopResourceGroup(
      {
        name: "rg-sre-agent-workshop-demo",
        tags: { "Workshop-Architecture": "single-vm" },
      },
      config,
    ),
    true,
  );
  assert.equal(
    isWorkshopResourceGroup(
      { name: "rg-sre-agent-workshop-demo", tags: { purpose: "production" } },
      config,
    ),
    false,
  );
});

test("environment inventory requires one bounded VM and workspace", () => {
  const resources = [
    { id: VM_ID, name: "vm-orders-demo", type: "Microsoft.Compute/virtualMachines" },
    {
      id:
        "/subscriptions/33333333-3333-4333-8333-333333333333/resourceGroups/" +
        "rg-sre-agent-workshop-demo/providers/Microsoft.OperationalInsights/workspaces/law-demo",
      name: "law-demo",
      type: "Microsoft.OperationalInsights/workspaces",
    },
    {
      id:
        "/subscriptions/33333333-3333-4333-8333-333333333333/resourceGroups/" +
        "rg-sre-agent-workshop-demo/providers/Microsoft.App/agents/sre-demo",
      name: "sre-demo",
      type: "Microsoft.App/agents",
    },
  ];
  const selected = selectEnvironmentResources(resources);
  assert.equal(selected.virtualMachine.name, "vm-orders-demo");
  assert.equal(selected.workspace.name, "law-demo");
  assert.equal(selected.sreAgent.name, "sre-demo");
  assert.equal(
    sreAgentIncidentsUrl(selected.sreAgent.id),
    `https://sre.azure.com/agents${selected.sreAgent.id}/views/incidents`,
  );
  assert.throws(() => selectEnvironmentResources(resources.slice(1)), /workshop VM/);
});

test("fault commands preserve fixed safety parameters and correlation", () => {
  assert.equal(
    createFaultScript("cpu", REQUEST),
    "set -euo pipefail\n" +
      "/usr/bin/python3 /opt/orders-api/faults.py cpu 600 2 " +
      `--request-id ${REQUEST}`,
  );
  assert.match(createFaultScript("disk", REQUEST), /disk 90 600/);
  assert.match(createFaultScript("reset", REQUEST), /faults\.py reset --request-id/);
  assert.throws(() => createFaultScript("shell", REQUEST), /not supported/);
  assert.throws(() => createFaultScript("cpu", "unsafe"), /request ID/);
  assert.throws(
    () => buildCpuMetricsUrl(`${VM_ID}?api-version=unsafe`),
    /resource ID/,
  );
});

test("run command success must be unique, verified, and correlated", () => {
  const response = {
    value: [
      {
        code: "ComponentStatus/StdOut/succeeded",
        level: "Info",
        message: `WORKSHOP_RESULT:${REQUEST}:{"ok":true,"cpu":"active"}`,
      },
    ],
  };
  assert.equal(parseRunCommandResult(response, REQUEST).cpu, "active");
  assert.throws(
    () =>
      parseRunCommandResult(
        {
          value: [
            ...response.value,
            ...response.value,
          ],
        },
        REQUEST,
      ),
    /one correlated/,
  );
  assert.throws(
    () =>
      parseRunCommandResult(
        {
          value: [
            {
              code: "ComponentStatus/StdErr/failed",
              level: "Error",
              message: "permission denied",
            },
          ],
        },
        REQUEST,
      ),
    /permission denied/,
  );
});

test("CPU metrics request is a 30-minute one-minute Average query", () => {
  const end = new Date("2026-09-25T00:30:00.000Z");
  const url = new URL(buildCpuMetricsUrl(VM_ID, end));
  assert.equal(url.hostname, "management.azure.com");
  assert.equal(url.searchParams.get("metricnames"), "Percentage CPU");
  assert.equal(url.searchParams.get("aggregation"), "Average");
  assert.equal(url.searchParams.get("interval"), "PT1M");
  assert.equal(
    url.searchParams.get("timespan"),
    "2026-09-25T00:00:00.000Z/2026-09-25T00:30:00.000Z",
  );
});

test("disk query is scoped to the selected VM and managed mount", () => {
  const query = buildDiskQuery(VM_ID);
  assert.match(query, /ago\(30m\)/);
  assert.match(query, /bin\(TimeGenerated, 1m\)/);
  assert.match(query, /InstanceName == '\/var\/lib\/orders'/);
  assert.ok(query.includes(VM_ID));
});

test("metric and log responses produce ordered numeric chart samples", () => {
  assert.deepEqual(
    parseMetricSeries({
      value: [
        {
          timeseries: [
            {
              data: [
                { timeStamp: "2026-09-25T00:02:00Z", average: 50 },
                { timeStamp: "2026-09-25T00:01:00Z", average: 25 },
                { timeStamp: "2026-09-25T00:03:00Z", average: null },
              ],
            },
          ],
        },
      ],
    }).map((point) => point.value),
    [25, 50],
  );

  assert.deepEqual(
    parseLogSeries({
      tables: [
        {
          columns: [{ name: "TimeGenerated" }, { name: "AverageFreeSpace" }],
          rows: [
            ["2026-09-25T00:02:00Z", 9.5],
            ["2026-09-25T00:01:00Z", 47.2],
            ["2026-09-25T00:03:00Z", null],
          ],
        },
      ],
    }).map((point) => point.value),
    [47.2, 9.5],
  );
});

test("both incident modules keep inline launchers and terminal fallbacks", () => {
  for (const [scenario, path, command] of [
    [
      "cpu",
      "../../docs/sre/03-incident-high-cpu/index.md",
      "python scripts/workshop.py fault cpu 600 2",
    ],
    [
      "disk",
      "../../docs/sre/04-incident-data-disk/index.md",
      "python scripts/workshop.py fault disk 90 600",
    ],
  ]) {
    const source = readFileSync(new URL(path, import.meta.url), "utf8");
    assert.ok(source.includes(`data-sre-incident="${scenario}"`));
    assert.ok(source.includes(command));
  }
});
