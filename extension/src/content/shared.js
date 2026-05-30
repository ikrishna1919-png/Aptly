// Shared field-detection + filling helpers. v1.0 only wires these up for
// Greenhouse, but the field model is platform-agnostic so adapters can reuse
// it later. NOTHING here mutates the host page except value-filling triggered
// by an explicit user action in the popup.

import { SENSITIVE_PATTERNS, SALARY_PATTERNS } from "../lib/config.js";

// Categories the popup colour-codes:
//   contact   → fill from profile, high confidence (green)
//   resume    → resume content / file upload
//   qa        → free/clustered question (yellow if suggested, red if novel)
export const CONTACT_FIELDS = {
  name: ["full name", "your name", "name"],
  first_name: ["first name", "given name"],
  last_name: ["last name", "family name", "surname"],
  email: ["email"],
  phone: ["phone", "mobile", "telephone"],
  linkedin: ["linkedin"],
  github: ["github"],
  portfolio: ["portfolio", "website", "personal site"],
  location: ["location", "city", "where are you based"],
};

function norm(s) {
  return (s || "").trim().toLowerCase().replace(/\s+/g, " ");
}

// Find the human-readable question for an input by walking the DOM the way
// Greenhouse structures it: <label for=id>, then an ancestor label, then the
// nearest preceding label-ish text.
export function questionFor(el) {
  const id = el.getAttribute("id");
  if (id) {
    const lab = document.querySelector(`label[for="${CSS.escape(id)}"]`);
    if (lab && lab.textContent.trim()) return clean(lab.textContent);
  }
  const ariaLabel = el.getAttribute("aria-label");
  if (ariaLabel) return clean(ariaLabel);
  const labelledby = el.getAttribute("aria-labelledby");
  if (labelledby) {
    const node = document.getElementById(labelledby);
    if (node) return clean(node.textContent);
  }
  const wrapLabel = el.closest("label");
  if (wrapLabel && wrapLabel.textContent.trim()) return clean(wrapLabel.textContent);
  // Greenhouse wraps fields in a container; grab its label/legend.
  const field = el.closest(".field, [class*='field'], fieldset, .application-question");
  if (field) {
    const lab = field.querySelector("label, legend, .label");
    if (lab && lab.textContent.trim()) return clean(lab.textContent);
  }
  return clean(el.getAttribute("name") || el.getAttribute("placeholder") || "");
}

function clean(text) {
  return (text || "")
    .replace(/\*/g, "")
    .replace(/\(required\)/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

// ── Page-wide, selector-agnostic field detection ───────────────────────────
// Works on both the old boards.greenhouse.io <form> markup and the new
// job-boards.greenhouse.io React SPA (which may not wrap fields in a <form>).
// Pure functions (DOM-in, value-out) so they're unit-testable with a stub DOM.

const SKIP_TYPES = new Set(["hidden", "submit", "button", "reset", "image"]);
const SKIP_PLACEHOLDER = /search|subscribe|newsletter|language|filter|keyword|find jobs/i;
const SKIP_NAME = /search|query|locale|lang|subscribe|newsletter/i;

export function isVisible(el) {
  // offsetParent is null for display:none + detached nodes; honour aria-hidden
  // and zero-size, with a client-rect fallback for position:fixed elements.
  if (el.getAttribute("aria-hidden") === "true") return false;
  if (el.offsetParent != null) return true;
  const r = el.getBoundingClientRect ? el.getBoundingClientRect() : { width: 0, height: 0 };
  return r.width > 0 && r.height > 0;
}

export function inChrome(el) {
  return !!(el.closest && el.closest("nav, header, footer, [role='navigation'], [role='search']"));
}

export function isApplicationInput(el) {
  const tag = (el.tagName || "").toLowerCase();
  if (!["input", "select", "textarea"].includes(tag)) return false;
  if (el.disabled) return false;
  const t = (el.getAttribute("type") || "").toLowerCase();
  if (SKIP_TYPES.has(t)) return false;
  if (tag === "input" && (t === "search" || el.getAttribute("role") === "searchbox")) return false;
  if (SKIP_PLACEHOLDER.test(el.getAttribute("placeholder") || "")) return false;
  if (SKIP_NAME.test(el.getAttribute("name") || "")) return false;
  if (inChrome(el)) return false;
  if (!isVisible(el)) return false;
  return true;
}

export function fieldType(el) {
  const tag = el.tagName.toLowerCase();
  if (tag === "textarea") return "text";
  if (tag === "select") return "select";
  if (tag === "input") {
    const t = (el.getAttribute("type") || "text").toLowerCase();
    if (t === "radio") return "radio";
    if (t === "checkbox") return "checkbox";
    if (t === "file") return "file";
    if (t === "date") return "date";
    return "text";
  }
  return "text";
}

export function isSensitive(question) {
  const q = norm(question);
  return SENSITIVE_PATTERNS.some((p) => q.includes(p));
}

export function isSalary(question) {
  const q = norm(question);
  return SALARY_PATTERNS.some((p) => q.includes(p));
}

export function contactKeyFor(question) {
  const q = norm(question);
  for (const [key, patterns] of Object.entries(CONTACT_FIELDS)) {
    if (patterns.some((p) => q === p || q.includes(p))) return key;
  }
  return null;
}

// React-friendly value setters: set via the native descriptor then dispatch
// input/change so controlled components pick up the new value.
export function setInputValue(el, value) {
  const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
  if (setter) setter.call(el, value);
  else el.value = value;
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

export function setSelectValue(el, value) {
  const v = norm(value);
  const opt = Array.from(el.options).find(
    (o) => norm(o.textContent) === v || norm(o.value) === v,
  );
  if (opt) {
    el.value = opt.value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
    return true;
  }
  return false;
}

export function clickRadioOrCheckbox(groupEls, value) {
  const v = norm(value);
  for (const el of groupEls) {
    const q = norm(questionFor(el)) || norm(el.value);
    if (q === v || q.includes(v) || norm(el.value) === v) {
      el.click(); // click triggers React handlers; .checked= doesn't
      return true;
    }
  }
  return false;
}
