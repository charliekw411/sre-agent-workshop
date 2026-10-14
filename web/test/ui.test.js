import test from "node:test";
import assert from "node:assert/strict";

import { SCENARIOS } from "../src/core.js";
import { HeaderConnection, renderLineChart } from "../src/ui.js";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.textContent = "";
    this.dataset = {};
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
