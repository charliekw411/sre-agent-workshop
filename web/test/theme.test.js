import test from "node:test";
import assert from "node:assert/strict";

import {
  restoreTheme,
  selectTheme,
  ThemePicker,
  THEMES,
  THEME_STORAGE_KEY,
} from "../src/theme.js";

class FakeNode {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.attributes = {};
    this.listeners = new Map();
    this.className = "";
    this.textContent = "";
    this.parent = null;
  }

  get isConnected() {
    return Boolean(this.connected || this.parent?.isConnected);
  }

  get nextSibling() {
    return this.parent?.children[this.parent.children.indexOf(this) + 1] || null;
  }

  append(...children) {
    for (const child of children) {
      child.parent = this;
      this.children.push(child);
    }
  }

  insertBefore(child, before) {
    child.parent = this;
    const index = this.children.indexOf(before);
    this.children.splice(index < 0 ? this.children.length : index, 0, child);
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  addEventListener(name, listener) {
    const listeners = this.listeners.get(name) || [];
    listeners.push(listener);
    this.listeners.set(name, listeners);
  }

  dispatch(name, event = {}) {
    event.target ||= this;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    for (const listener of this.listeners.get(name) || []) {
      listener(event);
    }
    return event;
  }

  contains(node) {
    return this === node || this.children.some((child) => child.contains(node));
  }

  querySelector(selector) {
    if (selector.startsWith(".") && this.className.split(" ").includes(selector.slice(1))) {
      return this;
    }
    for (const child of this.children) {
      const found = child.querySelector(selector);
      if (found) {
        return found;
      }
    }
    return null;
  }

  focus() {
    document.activeElement = this;
  }
}

function fixture(t, saved = null) {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const values = new Map(saved === null ? [] : [[THEME_STORAGE_KEY, saved]]);
  const document = new FakeNode("document");
  document.documentElement = new FakeNode("html");
  document.createElement = (tag) => new FakeNode(tag);
  const header = new FakeNode("nav");
  header.className = "md-header__inner";
  header.connected = true;
  const search = new FakeNode("div");
  search.className = "md-search";
  const source = new FakeNode("div");
  source.className = "md-header__source";
  header.append(search, source);
  document.append(header);
  const window = new FakeNode("window");
  window.localStorage = {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  };
  globalThis.document = document;
  globalThis.window = window;
  const warn = t.mock.method(console, "warn", () => {});
  t.after(() => {
    globalThis.document = previousDocument;
    globalThis.window = previousWindow;
  });
  return { document, window, header, search, source, values, warn };
}

test("a new visitor gets Vanilla without saving an unsolicited preference", (t) => {
  const { document, values } = fixture(t);
  assert.equal(restoreTheme(), "vanilla");
  assert.equal(document.documentElement.dataset.sreTheme, "vanilla");
  assert.equal(values.size, 0);
  assert.deepEqual(THEMES.map((theme) => theme.id), [
    "vanilla", "prism", "fieldnotes", "nightshift",
  ]);
});

test("all four selected themes persist and restore before the next page mounts", (t) => {
  const { document, values } = fixture(t);
  for (const theme of THEMES) {
    assert.equal(selectTheme(theme.id), true);
    assert.equal(values.get(THEME_STORAGE_KEY), theme.id);
    document.documentElement.dataset.sreTheme = "";
    assert.equal(restoreTheme(), theme.id);
  }
});

test("an unknown stored preference logs the problem and restores Vanilla", (t) => {
  const { document, warn } = fixture(t, "not-a-theme");
  assert.equal(restoreTheme(), "vanilla");
  assert.equal(document.documentElement.dataset.sreTheme, "vanilla");
  assert.match(warn.mock.calls[0].arguments[0], /unknown/);
});

test("invalid selections are rejected without changing appearance or storage", (t) => {
  const { document, values } = fixture(t, "prism");
  restoreTheme();
  assert.throws(() => selectTheme("auto"), RangeError);
  assert.equal(document.documentElement.dataset.sreTheme, "prism");
  assert.equal(values.get(THEME_STORAGE_KEY), "prism");
});

test("blocked storage cannot prevent the page or theme picker from working", (t) => {
  const { document, window, warn } = fixture(t);
  Object.defineProperty(window, "localStorage", {
    get() { throw new Error("Storage is blocked"); },
  });
  assert.equal(restoreTheme(), "vanilla");
  assert.equal(selectTheme("nightshift"), false);
  assert.equal(document.documentElement.dataset.sreTheme, "nightshift");
  const picker = new ThemePicker();
  assert.equal(picker.mount(), true);
  assert.match(picker.status.textContent, /storage is unavailable/i);
  assert.equal(warn.mock.calls.length, 2);
});

test("the picker mounts once immediately beside Search", (t) => {
  const { header, search } = fixture(t);
  const picker = new ThemePicker();
  assert.equal(picker.mount(), true);
  assert.equal(search.nextSibling, picker.root);
  assert.equal(picker.button.attributes["aria-haspopup"], "dialog");
  assert.equal(picker.inputs.size, 4);
  assert.equal(picker.inputs.get("vanilla").checked, true);
  assert.equal(picker.mount(), false);
  assert.equal(header.children.filter((node) => node.className === "sre-themes").length, 1);
});

test("opening focuses the current choice and Escape returns focus to its trigger", (t) => {
  const { document } = fixture(t, "fieldnotes");
  const picker = new ThemePicker();
  picker.mount();
  picker.button.dispatch("keydown", { key: "ArrowDown" });
  assert.equal(picker.panel.hidden, false);
  assert.equal(picker.button.attributes["aria-expanded"], "true");
  assert.equal(document.activeElement, picker.inputs.get("fieldnotes"));
  const event = document.dispatch("keydown", { key: "Escape" });
  assert.equal(event.defaultPrevented, true);
  assert.equal(picker.panel.hidden, true);
  assert.equal(document.activeElement, picker.button);
});

test("choosing a card updates the page, header, radio state, and saved preference", (t) => {
  const { document, values } = fixture(t);
  const picker = new ThemePicker();
  picker.mount();
  picker.open();
  const input = picker.inputs.get("prism");
  input.checked = true;
  input.dispatch("change");
  assert.equal(document.documentElement.dataset.sreTheme, "prism");
  assert.equal(values.get(THEME_STORAGE_KEY), "prism");
  assert.equal(picker.name.textContent, "Prism");
  assert.equal(picker.inputs.get("vanilla").checked, false);
  assert.match(picker.status.textContent, /Prism selected.*saved/);
  assert.equal(picker.panel.hidden, false);
});

test("failed preference saves report that the theme may not survive a reload", (t) => {
  const { document, window, warn } = fixture(t);
  const picker = new ThemePicker();
  picker.mount();
  window.localStorage.setItem = () => { throw new Error("Storage is full"); };
  const input = picker.inputs.get("fieldnotes");
  input.checked = true;
  input.dispatch("change");
  assert.equal(document.documentElement.dataset.sreTheme, "fieldnotes");
  assert.match(picker.status.textContent, /may not survive a reload/);
  assert.equal(warn.mock.calls.length, 1);
});

test("outside clicks and tabbing away close the panel without stealing focus", (t) => {
  const { document } = fixture(t);
  const picker = new ThemePicker();
  picker.mount();
  picker.open();
  const outside = new FakeNode("input");
  outside.focus();
  document.dispatch("click", { target: outside });
  assert.equal(picker.panel.hidden, true);
  assert.equal(document.activeElement, outside);
  picker.open();
  outside.focus();
  picker.root.dispatch("focusout", { relatedTarget: outside });
  assert.equal(picker.panel.hidden, true);
  assert.equal(document.activeElement, outside);
});

test("another tab can synchronize a theme or clear the preference back to Vanilla", (t) => {
  const { window, values, document } = fixture(t);
  const picker = new ThemePicker();
  picker.mount();
  values.set(THEME_STORAGE_KEY, "nightshift");
  window.dispatch("storage", { key: THEME_STORAGE_KEY });
  assert.equal(picker.name.textContent, "Nightshift");
  assert.equal(document.documentElement.dataset.sreTheme, "nightshift");
  values.clear();
  window.dispatch("storage", { key: null });
  assert.equal(picker.name.textContent, "Vanilla");
  assert.equal(document.documentElement.dataset.sreTheme, "vanilla");
});

test("replacing the header remounts the picker without duplicating document listeners", (t) => {
  const { document, header } = fixture(t, "prism");
  const picker = new ThemePicker();
  picker.mount();
  header.connected = false;
  header.parent = null;
  const replacement = new FakeNode("nav");
  replacement.className = "md-header__inner";
  replacement.connected = true;
  const search = new FakeNode("div");
  search.className = "md-search";
  replacement.append(search);
  document.children = [];
  document.append(replacement);
  assert.equal(picker.mount(), true);
  assert.equal(search.nextSibling, picker.root);
  assert.equal(picker.name.textContent, "Prism");
  assert.equal(document.listeners.get("click").length, 1);
  assert.equal(document.listeners.get("keydown").length, 1);
});
