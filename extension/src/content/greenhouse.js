// Greenhouse content script (v1.0). Detects the application form, reports the
// field count to the popup via the badge, and — only when the user clicks
// "Start filling" in the popup — fills fields it can and colour-codes each one.
//
// Hard rules: never submits; never auto-fills sensitive/demographic fields
// without opt-in; never modifies the page beyond filling values + a tiny
// status dot next to each touched field.

import {
  questionFor,
  fieldType,
  contactKeyFor,
  isSensitive,
  isApplicationInput,
  setInputValue,
  setSelectValue,
  clickRadioOrCheckbox,
} from "./shared.js";

const STATE = {
  fields: [],
  filledOnce: false,
  lastCount: -1,
};

const DOT_CLASS = "aptly-status-dot";
const COLORS = { green: "#16a34a", yellow: "#eab308", red: "#dc2626" };
const LOG = "[Aptly]";

// Minimum form-like inputs for a page to count as an application form. Fewer
// than this is probably just a search box / login, not an application.
const MIN_FIELDS = 3;

// Selector-AGNOSTIC detection: scan the whole document (works on the old
// boards.greenhouse.io <form> markup AND the new job-boards.greenhouse.io
// React SPA, which may not wrap fields in a <form> at all). Searching from a
// found <form> when one exists narrows noise; otherwise scan the document.
function collectFields() {
  const form = document.querySelector(
    "#application_form, #application-form, form[action*='application'], form",
  );
  const root = form || document;
  const els = Array.from(root.querySelectorAll("input, select, textarea")).filter(
    isApplicationInput,
  );
  // Group radios/checkboxes by name so a group counts as one question.
  const seenGroups = new Set();
  const fields = [];
  for (const el of els) {
    const type = fieldType(el);
    if ((type === "radio" || type === "checkbox") && el.name) {
      if (seenGroups.has(el.name)) continue;
      seenGroups.add(el.name);
      fields.push({ el, type, group: els.filter((x) => x.name === el.name) });
    } else {
      fields.push({ el, type, group: null });
    }
  }
  return fields;
}

function setDot(el, color) {
  let dot = el.parentElement?.querySelector(`.${DOT_CLASS}`);
  if (!dot) {
    dot = document.createElement("span");
    dot.className = DOT_CLASS;
    dot.style.cssText =
      "display:inline-block;width:8px;height:8px;border-radius:50%;margin-left:6px;vertical-align:middle;";
    el.insertAdjacentElement("afterend", dot);
  }
  dot.style.background = color;
  dot.title =
    color === COLORS.green
      ? "Filled from your Aptly profile"
      : color === COLORS.yellow
        ? "Suggested from a previous application — please review"
        : "Needs your input";
}

// Run detection, push the count to the background (badge) + log when it
// changes. `hasForm` is true once we clear the MIN_FIELDS threshold — the popup
// keys off this, not off the presence of a literal <form> element.
function detectFormFields(reason) {
  STATE.fields = collectFields();
  const count = STATE.fields.length;
  const hasForm = count >= MIN_FIELDS;
  if (count !== STATE.lastCount) {
    console.log(`${LOG} ${reason}: ${count} field${count === 1 ? "" : "s"} found`);
    STATE.lastCount = count;
  }
  chrome.runtime.sendMessage({ type: "GH_FIELDS", count, hasForm, url: location.href });
  return count;
}

let detectTimeout = null;

// The new Greenhouse pages mount the form after initial load (React). Watch the
// whole body and re-detect, debounced, until the DOM settles.
function init() {
  console.log(`${LOG} Content script loaded on ${location.href}`);
  detectFormFields("Initial form detection");

  const observer = new MutationObserver(() => {
    // Re-validate previously-filled fields; mark reverted ones red.
    if (STATE.filledOnce) {
      for (const f of STATE.fields) {
        if (f.type === "text" && f.filled && f.el && !f.el.value) setDot(f.el, COLORS.red);
      }
    }
    clearTimeout(detectTimeout);
    detectTimeout = setTimeout(() => {
      console.log(`${LOG} DOM changed, re-detecting...`);
      detectFormFields("After re-detection");
    }, 300);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

async function fillFields({ profile, resume, prefs }) {
  const summary = { green: 0, yellow: 0, red: 0, sensitive: 0, file: 0 };
  for (const f of STATE.fields) {
    const question = questionFor(f.el);
    if (!question) continue;

    // Sensitive/demographic: never auto-fill unless opted in.
    if (isSensitive(question) && !prefs.rememberDemographics) {
      setDot(f.el, COLORS.yellow);
      summary.sensitive++;
      continue;
    }

    // File upload: MV3 can't attach files programmatically. Flag for the
    // download-then-drop flow instead of failing silently.
    if (f.type === "file") {
      setDot(f.el, COLORS.yellow);
      summary.file++;
      continue;
    }

    // Contact info → profile (high confidence).
    const key = contactKeyFor(question);
    if (key && profile) {
      const value = contactValue(key, profile);
      if (value) {
        applyValue(f, value);
        f.filled = true;
        setDot(f.el, COLORS.green);
        summary.green++;
        continue;
      }
    }

    // Everything else → QA lookup (clustered learning loop). Salary asks
    // before saving but can still be suggested if previously answered.
    try {
      const res = await chrome.runtime.sendMessage({
        type: "QA_LOOKUP",
        question_text: question,
        field_type: f.type,
      });
      if (res && res.answer) {
        applyValue(f, res.answer);
        f.filled = true;
        setDot(f.el, COLORS.yellow); // suggested → review
        summary.yellow++;
      } else {
        setDot(f.el, COLORS.red);
        summary.red++;
      }
    } catch (_) {
      setDot(f.el, COLORS.red);
      summary.red++;
    }
  }
  STATE.filledOnce = true;
  return summary;
}

function contactValue(key, p) {
  switch (key) {
    case "name":
      return p.name;
    case "first_name":
      return (p.name || "").split(" ")[0];
    case "last_name":
      return (p.name || "").split(" ").slice(1).join(" ");
    case "email":
      return p.email;
    case "phone":
      return p.phone;
    case "linkedin":
      return p.linkedin;
    case "github":
      return p.github;
    case "portfolio":
      return p.portfolio;
    case "location":
      return p.location;
    default:
      return "";
  }
}

function applyValue(f, value) {
  if (f.type === "select") return setSelectValue(f.el, value);
  if (f.type === "radio" || f.type === "checkbox")
    return clickRadioOrCheckbox(f.group || [f.el], value);
  return setInputValue(f.el, value);
}

// Messages from the popup / background.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "GH_PING") {
    // Re-detect FRESH on every popup open so a late-rendered form is picked up
    // even if the observer hasn't fired since the user last looked.
    const count = detectFormFields("Popup ping");
    sendResponse({ hasForm: count >= MIN_FIELDS, count, url: location.href });
    return true;
  }
  if (msg.type === "GH_FILL") {
    fillFields(msg.payload).then((summary) => sendResponse({ summary }));
    return true; // async
  }
  return false;
});

init();
