// Shared field-detection + filling helpers. The field model is platform-
// agnostic, so multiple adapters reuse it: the standard-DOM ATSes (Greenhouse,
// Lever, Ashby, SmartRecruiters) via content/greenhouse.js, and the
// experimental Workday adapter via content/workday.js. NOTHING here mutates the
// host page except value-filling triggered by an explicit user action in the
// popup.

import { SENSITIVE_PATTERNS, SALARY_PATTERNS } from "../lib/config.js";

// Categories the popup colour-codes:
//   contact   → fill from profile, high confidence (green)
//   resume    → resume content / file upload
//   qa        → free/clustered question (yellow if suggested, red if novel)
export const CONTACT_FIELDS = {
  name: ["full name", "your name", "name"],
  first_name: ["first name", "given name", "legal first name"],
  last_name: ["last name", "family name", "surname", "legal last name"],
  email: ["email"],
  phone: ["phone", "mobile", "telephone"],
  linkedin: ["linkedin"],
  github: ["github"],
  portfolio: ["portfolio", "website", "personal site"],
  location: ["location", "city", "where are you based", "current location"],
  country: ["country"],
};

// Compliance / EEO questions → profile keys. Sponsorship + work-authorization
// are "standard" answers; the EEO four are demographic and only filled when the
// user explicitly set them (see complianceValue). Matched against the
// normalized question text; order matters (most specific first).
export const COMPLIANCE_FIELDS = {
  requires_sponsorship: [
    "require sponsorship",
    "need sponsorship",
    "visa sponsorship",
    "sponsorship now or in the future",
    "will you now or in the future require",
  ],
  work_authorization: [
    "authorized to work",
    "legally authorized",
    "work authorization",
    "eligible to work",
  ],
  veteran_status: ["veteran", "protected veteran"],
  disability_status: ["disability", "disabled"],
  race_ethnicity: ["race", "ethnicity", "hispanic or latino"],
  gender: ["gender", "sex"],
};

// The EEO keys we NEVER auto-select unless the user explicitly set a value.
export const EEO_KEYS = new Set([
  "veteran_status",
  "disability_status",
  "race_ethnicity",
  "gender",
]);

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

// Map a contact key (from contactKeyFor) to the matching profile value. Shared
// by every adapter so they fill name/email/links identically. Pure.
export function contactValue(key, p) {
  if (!p) return "";
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
    case "country":
      // Profile has no dedicated country field; fill only if one is present
      // (never fabricated from location). Empty → the field is left untouched.
      return p.country || "";
    default:
      return "";
  }
}

// Which compliance/EEO key (if any) a question is asking about.
export function complianceKeyFor(question) {
  const q = norm(question);
  for (const [key, patterns] of Object.entries(COMPLIANCE_FIELDS)) {
    if (patterns.some((p) => q.includes(p))) return key;
  }
  return null;
}

// The profile value for a compliance key, or "" when unset. EEO keys return ""
// unless the user explicitly set them — the caller then leaves the field
// untouched (never auto-selects a demographic answer the user didn't choose).
export function complianceValue(key, p) {
  if (!p) return "";
  return (p[key] || "").trim();
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

// Set a native <select> by matching `value` against option TEXT (loose, via
// matchOptionByText) — for compliance/EEO dropdowns whose option wording varies
// across ATSes ("Yes, I require sponsorship" vs the saved "Yes"). Returns true
// only if an option actually matched + was selected. Never selects a fallback.
export function setSelectByText(el, value) {
  if (!value) return false;
  const options = Array.from(el.options || []);
  const match = matchOptionByText(options, value);
  if (!match) return false;
  el.value = match.value;
  el.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

// Match a desired value against a list of option-like nodes by their visible
// text. Used by the Workday adapter for custom (non-<select>) dropdowns, where
// the trigger opens a [role="listbox"] of [role="option"] nodes. Pure and
// DOM-light (only reads `.textContent`) so it's unit-testable with a stub DOM.
// Returns the matching node or null — never guesses past a guarded contains().
export function matchOptionByText(options, value) {
  const v = norm(value);
  if (!v) return null;
  const texts = options.map((o) => norm(o && o.textContent));
  // 1) Exact (normalized) match — the safe, common case. Wins over any loose
  //    match regardless of option order (e.g. "No" beats "Not sure").
  let i = texts.findIndex((t) => t === v);
  if (i >= 0) return options[i];
  // 2) Loose containment either direction, length-guarded to avoid 1-char
  //    noise. Handles a Workday label carrying a longer/shorter form than the
  //    saved value (e.g. "United States of America" vs "United States").
  if (v.length >= 2) {
    i = texts.findIndex((t) => t.length >= 2 && (t.includes(v) || v.includes(t)));
    if (i >= 0) return options[i];
  }
  return null;
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

// ── Deep (shadow-DOM-piercing) query ────────────────────────────────────────
// querySelectorAll on `root` PLUS a recursive descent into every element's OPEN
// .shadowRoot, concatenated. Modern ATSes (SmartRecruiters among them) can mount
// form fields inside open shadow roots, which a plain document.querySelectorAll
// cannot see — that's how detection can find 0 fields on a page that clearly has
// inputs. NOTE: CLOSED shadow roots expose no .shadowRoot and are unreachable to
// ANY extension; nothing we can do about those.
export function queryAllDeep(root, selector) {
  const out = [];
  const visit = (node) => {
    if (!node || typeof node.querySelectorAll !== "function") return;
    for (const el of node.querySelectorAll(selector)) out.push(el);
    // Descend into the open shadow root of every element in this subtree.
    for (const el of node.querySelectorAll("*")) {
      if (el.shadowRoot) visit(el.shadowRoot);
    }
  };
  visit(root);
  return out;
}

const DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document";

// ── Job-description extraction (read-only) ──────────────────────────────────
// Best-effort visible JD text from the CURRENT page, for the popup's review
// panel. NEVER mutates the page. Prefers obvious description containers, then
// falls back to the largest visible text block. Length-capped so a giant page
// can't bloat the message. The popup escapes this before rendering — it is
// untrusted third-party page content.
const JD_MAX_CHARS = 4000;

// Best-first containers that usually wrap a posting's description. The `i`
// flag makes the attribute substring match case-insensitive (CSS L4, Chrome).
const JD_CONTAINER_SELECTORS = [
  "[data-automation-id*='jobPostingDescription' i]", // Workday
  "[data-automation-id*='description' i]",
  "[class*='job-description' i]",
  "[class*='jobdescription' i]",
  "[class*='posting-description' i]",
  "[id*='job-description' i]",
  "[class*='description' i]",
  "article",
  "[role='main']",
  "main",
];

function visibleText(el) {
  if (!el || !isVisible(el)) return "";
  // innerText (not textContent) so hidden subtrees are excluded and block
  // breaks are preserved; collapse runaway blank lines.
  return (el.innerText || el.textContent || "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

export function extractJobDescription(root = document) {
  let best = "";
  for (const sel of JD_CONTAINER_SELECTORS) {
    let candidates = [];
    try {
      candidates = queryAllDeep(root, sel);
    } catch (_) {
      candidates = [];
    }
    for (const el of candidates) {
      if (inChrome(el)) continue; // skip nav/header/footer/search chrome
      const t = visibleText(el);
      if (t.length > best.length) best = t;
    }
    // A solid hit from a specific selector is enough — stop before the generic
    // [class*=description]/main catch-alls over-grab the whole layout.
    if (best.length >= 600) break;
  }
  // Fallback: largest visible text among a FEW structural blocks (then body).
  // Deliberately not every <div> — innerText forces layout, so keep it bounded.
  if (best.length < 200) {
    for (const el of queryAllDeep(root, "main, [role='main'], article, section")) {
      if (inChrome(el)) continue;
      const t = visibleText(el);
      if (t.length > best.length) best = t;
    }
    if (best.length < 200 && root && root.body) {
      const bodyText = visibleText(root.body);
      if (bodyText.length > best.length) best = bodyText;
    }
  }
  return best.slice(0, JD_MAX_CHARS);
}

// ── Resume file attach (user-initiated, part of "Start filling") ────────────
// Decode the base64 DOCX (fetched with the bearer token by the service worker —
// the binary crosses the messaging boundary as base64 because Chrome messaging
// is JSON-serialized) back into a File. atob is available in content scripts.
export function base64ToFile(b64, name, mime) {
  const binary = atob(b64 || "");
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new File([bytes], name || "Aptly_Resume.docx", { type: mime || DOCX_MIME });
}

// Attach a File to an <input type=file> the way the browser would — via a
// DataTransfer — then fire input+change so React/controlled widgets notice.
// Returns true only if the input actually holds the file afterwards.
export function attachFileToInput(input, file) {
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
    return !!(input.files && input.files.length > 0);
  } catch (_) {
    return false;
  }
}

// Fallback for styled drop-zones: synthesise a drag-and-drop carrying the file.
// Best-effort — we can't confirm the site accepted it — so callers prefer
// attachFileToInput when a real input exists and only fall here if that fails.
export function dropFileOnZone(zone, file) {
  if (!zone) return false;
  try {
    const dt = new DataTransfer();
    dt.items.add(file);
    for (const type of ["dragenter", "dragover", "drop"]) {
      zone.dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
    }
    return true;
  } catch (_) {
    return false;
  }
}

// The visible drop target wrapping a (often hidden) file input.
export function dropZoneFor(input) {
  return (
    (input.closest &&
      input.closest("label, [class*='upload'], [class*='drop'], [class*='attach'], [class*='file']")) ||
    input.parentElement ||
    null
  );
}

// True if `el` is a file input we'd attach a RESUME to — an <input type=file>
// that is NOT an obvious photo/avatar/image picker. Deliberately does NOT check
// visibility: resume inputs are routinely display:none behind a drop-zone, so
// detection skips them but attach must still find them. Pure → unit-testable.
export function isResumeFileInput(el) {
  const tag = (el.tagName || "").toLowerCase();
  const type = (el.getAttribute("type") || "").toLowerCase();
  if (tag !== "input" || type !== "file") return false;
  const hay = `${el.getAttribute("name") || ""} ${el.getAttribute("id") || ""}`.toLowerCase();
  if (/photo|avatar|image|picture|headshot|logo|profile.?pic/.test(hay)) return false;
  // Reject image-only pickers, but keep ones that also allow docs/pdf.
  const accept = (el.getAttribute("accept") || "").toLowerCase();
  if (accept && /image\//.test(accept) && !/(pdf|word|document|officedocument|\.docx?|\.pdf)/.test(accept)) {
    return false;
  }
  return true;
}

// All resume-eligible file inputs under `root`, piercing open shadow roots.
export function findFileInputs(root) {
  return queryAllDeep(root, "input[type='file']").filter(isResumeFileInput);
}
