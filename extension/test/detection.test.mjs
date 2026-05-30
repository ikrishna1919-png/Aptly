// Node test for the selector-agnostic detection heuristic (isApplicationInput +
// the MIN_FIELDS threshold). No browser / jsdom needed — a tiny element stub
// reproduces just the DOM surface the predicates touch (tagName, getAttribute,
// offsetParent, closest, disabled). Run: `node test/detection.test.mjs`.
import assert from "node:assert";
import { isApplicationInput } from "../src/content/shared.js";

let passed = 0;
function check(name, cond) {
  assert.ok(cond, name);
  passed++;
}

// Minimal element stub. `ancestors` is a CSS-ish tag/role list used by closest().
function el({
  tag = "input",
  type = "text",
  name = "",
  placeholder = "",
  role = "",
  ariaHidden = false,
  disabled = false,
  visible = true,
  ancestors = [],
} = {}) {
  const attrs = { type, name, placeholder, role, "aria-hidden": ariaHidden ? "true" : null };
  return {
    tagName: tag.toUpperCase(),
    disabled,
    getAttribute: (k) => (attrs[k] === undefined ? null : attrs[k]),
    // offsetParent: null when hidden (display:none semantics in the stub).
    offsetParent: visible ? {} : null,
    getBoundingClientRect: () => (visible ? { width: 200, height: 30 } : { width: 0, height: 0 }),
    // closest(selector) → true if any ancestor tag/role is named in the selector.
    closest: (selector) => {
      const wanted = selector.match(/[a-z-]+|role='[a-z]+'/gi) || [];
      return ancestors.some((a) =>
        wanted.some((w) => w === a || w === `role='${a}'` || w.includes(a)),
      ) ? {} : null;
    },
  };
}

// ── Accepts genuine application inputs ──────────────────────────────────────
check("plain text input accepted", isApplicationInput(el({ name: "first_name" })));
check("textarea accepted", isApplicationInput(el({ tag: "textarea", name: "cover_letter" })));
check("select accepted", isApplicationInput(el({ tag: "select", name: "school" })));
check("file (resume) accepted", isApplicationInput(el({ type: "file", name: "resume" })));
check("custom question accepted", isApplicationInput(el({ name: "question_4265" })));

// ── Rejects non-application inputs ──────────────────────────────────────────
check("hidden rejected", !isApplicationInput(el({ type: "hidden" })));
check("submit rejected", !isApplicationInput(el({ type: "submit" })));
check("disabled rejected", !isApplicationInput(el({ disabled: true })));
check("search type rejected", !isApplicationInput(el({ type: "search", name: "q" })));
check("searchbox role rejected", !isApplicationInput(el({ role: "searchbox" })));
check("search placeholder rejected", !isApplicationInput(el({ placeholder: "Search jobs" })));
check("subscribe name rejected", !isApplicationInput(el({ name: "newsletter_subscribe" })));
check("nav input rejected", !isApplicationInput(el({ name: "x", ancestors: ["nav"] })));
check("header input rejected", !isApplicationInput(el({ name: "x", ancestors: ["header"] })));
check("footer input rejected", !isApplicationInput(el({ name: "x", ancestors: ["footer"] })));
check("aria-hidden rejected", !isApplicationInput(el({ ariaHidden: true })));
check("invisible (display:none) rejected", !isApplicationInput(el({ visible: false })));
check("non-form element rejected", !isApplicationInput(el({ tag: "div" })));

// ── Whole-page scenarios: count app inputs, apply MIN_FIELDS=3 threshold ─────
const MIN_FIELDS = 3;
const countApp = (list) => list.filter(isApplicationInput).length;

// New job-boards.greenhouse.io application (no <form> wrapper) → 6 fields.
const newSpaPage = [
  el({ name: "first_name" }),
  el({ name: "last_name" }),
  el({ name: "email" }),
  el({ type: "tel", name: "phone" }),
  el({ type: "file", name: "resume" }),
  el({ tag: "select", name: "school" }),
  el({ type: "search", placeholder: "Search jobs", ancestors: ["header"] }), // site search
];
check("new SPA page detected as form (>=3)", countApp(newSpaPage) >= MIN_FIELDS);
check("new SPA page counts 6 app fields", countApp(newSpaPage) === 6);

// A search-only landing page → below threshold, NOT an application.
const searchOnly = [
  el({ type: "search", placeholder: "Search jobs", ancestors: ["header"] }),
  el({ type: "hidden", name: "csrf" }),
];
check("search-only page is NOT a form (<3)", countApp(searchOnly) < MIN_FIELDS);

// Old boards.greenhouse.io form → still detected (backward compatible).
const oldFormPage = [
  el({ name: "job_application[first_name]" }),
  el({ name: "job_application[last_name]" }),
  el({ name: "job_application[email]" }),
  el({ type: "file", name: "job_application[resume]" }),
];
check("old form page still detected", countApp(oldFormPage) >= MIN_FIELDS);

console.log(`detection.test: ${passed} assertions passed`);
