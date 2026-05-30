// Workday content script (EXPERIMENTAL / UNVERIFIED).
//
// ⚠️  Workday's application DOM could NOT be verified in-repo (no browser, no
// fixture). This adapter is written against Workday's *known/documented*
// structure — `data-automation-id` fields and custom button→listbox dropdowns —
// and MUST be tested on a real Workday application before its support is
// advertised or relied on. Until then the popup labels it "experimental".
//
// It reuses the platform-agnostic helpers + the GH_* message protocol from the
// standard-DOM adapter (shared.js / background.js / popup.js), and adds only the
// Workday-specific pieces:
//   * field discovery via data-automation-id + custom-widget triggers,
//   * a custom dropdown filler (open [role=listbox], click the matching
//     [role=option]) — these are NOT <select> elements,
//   * date/spinbutton + file fields left as "review" (yellow), never guessed.
//
// Same hard rules as greenhouse.js: never submits; never auto-fills
// sensitive/demographic fields without opt-in; never modifies the page beyond
// filling values + a tiny status dot next to each touched field.

import {
  questionFor,
  contactKeyFor,
  contactValue,
  isSensitive,
  isApplicationInput,
  isVisible,
  inChrome,
  setInputValue,
  setSelectValue,
  clickRadioOrCheckbox,
  matchOptionByText,
} from "./shared.js";

const STATE = {
  fields: [],
  filledOnce: false,
  lastCount: -1,
};

const DOT_CLASS = "aptly-status-dot";
const COLORS = { green: "#16a34a", yellow: "#eab308", red: "#dc2626" };
const LOG = "[Aptly·Workday]";

// Minimum form-like fields for a page to count as an application form.
const MIN_FIELDS = 3;

// How long to wait for a Workday custom dropdown's listbox to render after we
// click its trigger (it mounts async, often in a body-level popup layer).
const LISTBOX_TIMEOUT_MS = 1500;

// Selectors for Workday custom-widget triggers that behave like a <select> but
// are buttons/comboboxes opening a [role="listbox"]. Standard inputs are
// gathered separately through the shared isApplicationInput gate.
const CUSTOM_DROPDOWN_SELECTOR =
  "button[aria-haspopup='listbox'], [role='button'][aria-haspopup='listbox'], [role='combobox']";

// Classify a discovered node into how we should fill it. Pure (reads tag/type/
// role/data-automation-id only) — keeps fillFields readable.
function classifyWorkdayField(el) {
  const tag = (el.tagName || "").toLowerCase();
  const role = (el.getAttribute("role") || "").toLowerCase();
  const type = (el.getAttribute("type") || "").toLowerCase();
  const aid = (el.getAttribute("data-automation-id") || "").toLowerCase();

  if (tag === "select") return "select";
  if (tag === "textarea") return "text";
  if (tag === "input") {
    if (type === "file") return "file";
    // Workday dates are spinbutton inputs (month/day/year) or native date
    // pickers — never guess these.
    if (type === "date" || role === "spinbutton") return "review-date";
    if (type === "radio") return "radio";
    if (type === "checkbox") return "checkbox";
    return "text"; // text / email / tel / number / url
  }
  // Custom widgets (buttons / comboboxes). A Workday date picker is also a
  // button — route anything date-ish to review rather than the listbox filler.
  if (/date|month|day|year|calendar/.test(aid)) return "review-date";
  return "dropdown";
}

// Find the prompt text for a Workday field. Reuse the shared resolver first
// (label[for], aria-label/labelledby, wrapping label), then fall back to a
// label inside the closest data-automation-id group container.
function workdayQuestionFor(el) {
  const q = questionFor(el);
  if (q) return q;
  const group = el.closest && el.closest("[data-automation-id]");
  if (group) {
    const lab = group.querySelector("label, legend, [id$='label'], [data-automation-id$='label']");
    if (lab && lab.textContent.trim()) {
      return lab.textContent.replace(/\s+/g, " ").trim();
    }
  }
  return "";
}

// Discover fillable fields. Workday mounts the apply flow under an
// applyFlowPage container; fall back to the first <form>, then the document.
function collectWorkdayFields() {
  const root =
    document.querySelector("[data-automation-id='applyFlowPage'], form") || document;
  const nodes = Array.from(
    root.querySelectorAll(`input, select, textarea, ${CUSTOM_DROPDOWN_SELECTOR}`),
  );
  const seenGroups = new Set();
  const fields = [];
  for (const el of nodes) {
    const tag = (el.tagName || "").toLowerCase();
    const isStandard = ["input", "select", "textarea"].includes(tag);
    // Standard inputs go through the shared application-input gate; custom
    // widgets (buttons/comboboxes) aren't <input> so they bypass it, but must
    // still be visible and outside page chrome.
    if (isStandard) {
      if (!isApplicationInput(el)) continue;
    } else if (inChrome(el) || !isVisible(el)) {
      continue;
    }
    const kind = classifyWorkdayField(el);
    if ((kind === "radio" || kind === "checkbox") && el.name) {
      if (seenGroups.has(el.name)) continue;
      seenGroups.add(el.name);
      fields.push({ el, kind, group: nodes.filter((x) => x.name === el.name) });
    } else {
      fields.push({ el, kind, group: null });
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
        ? "Suggested or needs review — check this field carefully"
        : "Needs your input";
}

// Run detection, push the count to the background (badge) + log when it
// changes. Uses the same GH_FIELDS message the standard-DOM adapter does, so
// the existing badge handler in background.js works unchanged.
function detectFormFields(reason) {
  STATE.fields = collectWorkdayFields();
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

// Workday is a multi-step wizard rendered by React; fields mount after load and
// change per step. Watch the body + re-detect (debounced) on every step change.
function init() {
  console.log(`${LOG} Content script loaded on ${location.href} (experimental)`);
  detectFormFields("Initial form detection");

  const observer = new MutationObserver(() => {
    // Re-validate previously-filled text fields; mark reverted ones red.
    if (STATE.filledOnce) {
      for (const f of STATE.fields) {
        if (f.kind === "text" && f.filled && f.el && !f.el.value) setDot(f.el, COLORS.red);
      }
    }
    clearTimeout(detectTimeout);
    detectTimeout = setTimeout(() => {
      console.log(`${LOG} DOM changed, re-detecting (step change)...`);
      detectFormFields("After re-detection");
    }, 300);
  });
  observer.observe(document.body, { childList: true, subtree: true });
}

// Open a Workday custom dropdown and click the option matching `value`.
// Returns true only if an option was actually clicked. DOM-side + async (timers
// + clicks), so it isn't unit-tested; the *matching* is matchOptionByText,
// which is.
async function selectWorkdayDropdown(trigger, value) {
  try {
    trigger.click(); // open the listbox
  } catch (_) {
    return false;
  }
  const listbox = await waitForListbox(LISTBOX_TIMEOUT_MS);
  if (!listbox) return false;
  const options = Array.from(listbox.querySelectorAll("[role='option']"));
  const match = matchOptionByText(options, value);
  if (!match) {
    closeListbox(trigger);
    return false;
  }
  try {
    match.click();
  } catch (_) {
    return false;
  }
  return true;
}

// Poll for the first visible [role="listbox"] (Workday renders it in a popup
// layer, not inside the trigger). Resolves null on timeout.
function waitForListbox(timeoutMs) {
  return new Promise((resolve) => {
    const start = Date.now();
    (function poll() {
      const lb = Array.from(document.querySelectorAll("[role='listbox']")).find(isVisible);
      if (lb) return resolve(lb);
      if (Date.now() - start > timeoutMs) return resolve(null);
      setTimeout(poll, 60);
    })();
  });
}

// Dismiss an open listbox without selecting (Escape).
function closeListbox(trigger) {
  try {
    trigger.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  } catch (_) {
    /* ignore */
  }
}

// Apply a value to a Workday field by kind. Async because custom dropdowns
// await their listbox. Returns whether the value was actually applied.
async function applyWorkdayValue(f, value) {
  if (f.kind === "dropdown") return selectWorkdayDropdown(f.el, value);
  if (f.kind === "select") return setSelectValue(f.el, value);
  if (f.kind === "radio" || f.kind === "checkbox") {
    return clickRadioOrCheckbox(f.group || [f.el], value);
  }
  setInputValue(f.el, value);
  return true;
}

async function fillFields({ profile, prefs }) {
  const summary = { green: 0, yellow: 0, red: 0, sensitive: 0, file: 0 };
  for (const f of STATE.fields) {
    const question = workdayQuestionFor(f.el);
    if (!question) continue;

    // Sensitive/demographic: never auto-fill unless opted in.
    if (isSensitive(question) && !prefs.rememberDemographics) {
      setDot(f.el, COLORS.yellow);
      summary.sensitive++;
      continue;
    }

    // File upload: MV3 can't attach files programmatically — flag for review.
    if (f.kind === "file") {
      setDot(f.el, COLORS.yellow);
      summary.file++;
      continue;
    }

    // Dates / spinbuttons: never guess — leave for the user to review.
    if (f.kind === "review-date") {
      setDot(f.el, COLORS.yellow);
      summary.yellow++;
      continue;
    }

    // Contact info → profile (high confidence). Only mark green if the value
    // was actually applied (a dropdown may have no matching option).
    const key = contactKeyFor(question);
    if (key && profile) {
      const value = contactValue(key, profile);
      if (value && (await applyWorkdayValue(f, value))) {
        f.filled = true;
        setDot(f.el, COLORS.green);
        summary.green++;
        continue;
      }
    }

    // Everything else → QA lookup (clustered learning loop). A suggestion that
    // applies is yellow (review); anything unmatched is red (your input).
    try {
      const res = await chrome.runtime.sendMessage({
        type: "QA_LOOKUP",
        question_text: question,
        field_type: f.kind === "dropdown" ? "select" : f.kind,
      });
      if (res && res.answer && (await applyWorkdayValue(f, res.answer))) {
        f.filled = true;
        setDot(f.el, COLORS.yellow);
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

// Messages from the popup / background — same GH_* protocol as greenhouse.js.
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "GH_PING") {
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
