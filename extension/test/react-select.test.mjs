// Node test for the react-select driver (setReactSelectByText) + the EEO
// blank-skip rule. react-select can't be exercised in a real browser here, but
// the driver's logic (open → poll portal → match by text → commit → verify) is
// deterministic against a minimal DOM/event shim. Run:
//   node test/react-select.test.mjs

import assert from "node:assert";

let passed = 0;
function check(name, cond) {
  assert.ok(cond, name);
  passed++;
}

// ── Minimal DOM/event shim (only what shared.js touches) ─────────────────────
// Events: shared.js constructs PointerEvent/MouseEvent/KeyboardEvent and reads
// `.type`; nodes need addEventListener/dispatchEvent + querySelectorAll/closest.

class Evt {
  constructor(type) {
    this.type = type;
  }
}
globalThis.PointerEvent = class extends Evt {};
globalThis.MouseEvent = class extends Evt {};
globalThis.KeyboardEvent = class extends Evt {
  constructor(type, init = {}) {
    super(type);
    this.key = init.key;
  }
};
globalThis.window = {};

class Node {
  constructor({ tag = "div", cls = "", id = "", role = "", text = "" } = {}) {
    this.tag = tag;
    this.className = cls;
    this.id = id;
    this._role = role;
    this.textContent = text;
    this.children = [];
    this.parent = null;
    this._listeners = {};
    this.offsetParent = {}; // visible by default
    this.shadowRoot = null;
  }
  getAttribute(k) {
    if (k === "role") return this._role || null;
    if (k === "aria-hidden") return null;
    return null;
  }
  add(child) {
    child.parent = this;
    this.children.push(child);
    return child;
  }
  _all() {
    const out = [];
    const walk = (n) => {
      for (const c of n.children) {
        out.push(c);
        walk(c);
      }
    };
    walk(this);
    return out;
  }
  querySelectorAll(sel) {
    return this._all().filter((n) => _matches(n, sel));
  }
  querySelector(sel) {
    return this.querySelectorAll(sel)[0] || null;
  }
  closest(sel) {
    let n = this;
    while (n) {
      if (_matches(n, sel)) return n;
      n = n.parent;
    }
    return null;
  }
  getBoundingClientRect() {
    return { width: 100, height: 20 };
  }
  addEventListener(t, fn) {
    (this._listeners[t] = this._listeners[t] || []).push(fn);
  }
  dispatchEvent(e) {
    (this._listeners[e.type] || []).forEach((fn) => fn(e));
    return true;
  }
  focus() {}
}

// Tiny selector matcher covering the few forms shared.js uses:
//   [class*='x']  [id*='x']  [role='x']  tag  comma-lists
function _matches(node, selector) {
  return selector.split(",").some((partRaw) => {
    const part = partRaw.trim();
    let m;
    if ((m = part.match(/^\[class\*=['"]([^'"]+)['"]\]$/)))
      return (node.className || "").includes(m[1]);
    if ((m = part.match(/^\[id\*=['"]([^'"]+)['"]\]\[role=['"]([^'"]+)['"]\]$/)))
      return (node.id || "").includes(m[1]) && node._role === m[2];
    if ((m = part.match(/^\[role=['"]([^'"]+)['"]\]$/))) return node._role === m[1];
    if ((m = part.match(/^\[id\*=['"]([^'"]+)['"]\]$/))) return (node.id || "").includes(m[1]);
    return node.tag === part;
  });
}

// Build a react-select: control > combobox input, plus a body-portal that mounts
// option nodes only AFTER the control receives pointerdown (lazy menu).
function buildReactSelect(optionTexts) {
  const body = new Node({ tag: "body" });
  globalThis.document = body;

  const control = new Node({ cls: "select__control" });
  const input = new Node({ tag: "input", role: "combobox", id: "question_123" });
  control.add(input);
  const valueHolder = new Node({ cls: "select__single-value", text: "" });
  control.add(valueHolder);
  body.add(control);

  const portal = new Node({ id: "react-select-portal" });
  body.add(portal);

  let open = false;
  control.addEventListener("pointerdown", () => {
    if (open) return;
    open = true;
    optionTexts.forEach((t, i) => {
      const o = new Node({
        id: `react-select-2-option-${i}`,
        role: "option",
        cls: "select__option",
        text: t,
      });
      // committing an option updates the control's rendered value
      o.addEventListener("mouseup", () => {
        valueHolder.textContent = t;
      });
      portal.add(o);
    });
  });
  return { input, control, valueHolder };
}

const { setReactSelectByText } = await import("../src/content/shared.js");

// (a) opens, finds a portal option by text, selects it, confirms commit.
{
  const { input, valueHolder } = buildReactSelect([
    "Please select",
    "Yes, I require sponsorship now or in the future",
    "No",
  ]);
  const ok = await setReactSelectByText(input, "Yes", { waitMs: 800 });
  check("react-select 'Yes' committed", ok === true);
  check(
    "control shows the chosen option",
    valueHolder.textContent === "Yes, I require sponsorship now or in the future",
  );
}

// (a2) no matching option → returns false, control left untouched.
{
  const { input, valueHolder } = buildReactSelect(["Male", "Female"]);
  const ok = await setReactSelectByText(input, "Non-binary", { waitMs: 400 });
  check("no match → false", ok === false);
  check("no match → control untouched", valueHolder.textContent === "");
}

// (a3) blank value → never opens / never commits.
{
  const { input, valueHolder } = buildReactSelect(["A", "B"]);
  const ok = await setReactSelectByText(input, "", { waitMs: 200 });
  check("blank value → false", ok === false);
  check("blank → untouched", valueHolder.textContent === "");
}

console.log(`react-select.test: ${passed} assertions passed`);
