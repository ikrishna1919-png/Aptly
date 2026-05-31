// Standard-DOM ATS content script. Despite the filename (kept as-is this
// release to avoid a churny rename), this now serves every ATS that exposes a
// conventional <input>/<select>/<textarea> form: Greenhouse, Lever, Ashby, and
// SmartRecruiters. Detection + fill are selector-agnostic (see shared.js), so
// one script covers all four. Workday is handled separately by workday.js
// because its custom-widget DOM needs bespoke fillers.
//
// Detects the application form, reports the field count to the popup via the
// badge, and — only when the user clicks "Start filling" in the popup — fills
// fields it can and colour-codes each one.
//
// Hard rules: never submits; never auto-fills sensitive/demographic fields
// without opt-in; never modifies the page beyond filling values + a tiny
// status dot next to each touched field.

import {
  questionFor,
  fieldType,
  contactKeyFor,
  contactValue,
  isSensitive,
  isApplicationInput,
  setInputValue,
  setSelectValue,
  clickRadioOrCheckbox,
  queryAllDeep,
  findFileInputs,
  base64ToFile,
  attachFileToInput,
  dropFileOnZone,
  dropZoneFor,
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

// Selector-AGNOSTIC, shadow-DOM-piercing detection. We ALWAYS scan the whole
// document with queryAllDeep (which descends into OPEN shadow roots) and filter
// with isApplicationInput. This is a superset of any single-<form> scan, so it
// can't under-count, and it removes the wrong-form hijack where a stray
// non-application <form> with >=3 inputs (search/login/newsletter) used to
// satisfy the threshold and block the real apply fields — e.g. SmartRecruiters,
// whose fields (id="first-name-input", …) are mounted in an open shadow root.
// Works on old Greenhouse <form> markup, the React SPA, Lever/Ashby, and
// SmartRecruiters alike. (Closed shadow roots are unreachable to any extension.)
function collectFields() {
  const els = queryAllDeep(document, "input, select, textarea").filter(isApplicationInput);
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

async function fillFields({ profile, runId, prefs }) {
  const summary = { green: 0, yellow: 0, red: 0, sensitive: 0, file: 0, fileAttached: 0, attachedName: "" };
  for (const f of STATE.fields) {
    const question = questionFor(f.el);
    if (!question) continue;

    // Sensitive/demographic: never auto-fill unless opted in.
    if (isSensitive(question) && !prefs.rememberDemographics) {
      setDot(f.el, COLORS.yellow);
      summary.sensitive++;
      continue;
    }

    // File inputs are handled by the dedicated resume-attach pass after this
    // loop (it also covers HIDDEN inputs behind drop-zones, which detection
    // skips because they aren't visible).
    if (f.type === "file") continue;

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
  await attachResume(runId, document, summary);
  STATE.filledOnce = true;
  return summary;
}

function applyValue(f, value) {
  if (f.type === "select") return setSelectValue(f.el, value);
  if (f.type === "radio" || f.type === "checkbox")
    return clickRadioOrCheckbox(f.group || [f.el], value);
  return setInputValue(f.el, value);
}

// Resume auto-attach: the service worker fetches the tailored DOCX (it holds the
// bearer token) and returns it base64-encoded; we decode it here and attach it
// to every resume file input — including hidden ones behind a styled drop-zone.
// User-initiated (part of Start filling); NEVER submits. Attached inputs go
// green (summary.fileAttached); on any failure we leave the field yellow and the
// popup shows the manual-download fallback (summary.file).
async function attachResume(runId, root, summary) {
  const inputs = findFileInputs(root);
  if (!inputs.length) return;
  if (!runId) {
    for (const el of inputs) {
      setDot(el, COLORS.yellow);
      summary.file++;
    }
    return;
  }
  let data = null;
  try {
    data = await chrome.runtime.sendMessage({ type: "RESUME_FILE", runId });
  } catch (_) {
    data = null;
  }
  if (!data || !data.base64) {
    for (const el of inputs) {
      setDot(el, COLORS.yellow);
      summary.file++;
    }
    return;
  }
  const file = base64ToFile(data.base64, data.filename, data.mime);
  for (const el of inputs) {
    let ok = attachFileToInput(el, file);
    if (!ok) ok = dropFileOnZone(dropZoneFor(el), file);
    if (ok) {
      setDot(el, COLORS.green);
      summary.fileAttached++;
      summary.attachedName = data.filename;
    } else {
      setDot(el, COLORS.yellow);
      summary.file++;
    }
  }
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
