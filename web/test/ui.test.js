import test from "node:test";
import assert from "node:assert/strict";

import { SCENARIOS } from "../src/core.js";
import { renderLineChart } from "../src/ui.js";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.attributes = {};
    this.children = [];
    this.className = "";
    this.textContent = "";
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
