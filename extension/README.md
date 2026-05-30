# Aptly Application Assistant — Chrome Extension

A Manifest V3 browser extension that helps you fill job application forms with
your Aptly profile and tailored resumes. **You review every field and click
submit yourself** — the extension never submits for you.

> **Supported ATSes:** Greenhouse, Lever, Ashby, and SmartRecruiters (one
> selector-agnostic content script covers all four standard-DOM forms).
> **Workday is experimental and unverified** — it ships behind a separate,
> clearly-labelled content script and must be tested on a real Workday
> application before it's relied on. Review every Workday field carefully.

## What it does

- Detects a job application form on a supported ATS and shows the field count
  on the toolbar badge.
- On your click, fills what it can and **colour-codes every field**:
  - 🟢 **green** — filled from your Aptly profile (name, email, links)
  - 🟡 **yellow** — a suggestion from a previous application; **review it**
  - 🔴 **red** — needs your input
- Learns answers to novel questions and reuses them across sites (the
  semantic-clustering "learning loop", served by the Aptly backend).
- Demographic/sensitive questions are **left blank** unless you explicitly opt
  in. Salary questions ask before saving.
- Resume file upload: browsers block extensions from attaching files
  programmatically (a security rule), so the popup gives you a one-click DOCX
  download to drop into the resume field.

## Privacy

- The extension stores **only** your bearer token and a couple of preference
  flags in `chrome.storage.local`. It never persists your profile, resume
  content, or saved answers — those are fetched from the backend per session
  and held in memory only.
- Auth uses a **separate per-device bearer token**, not your aptly.fyi session
  cookie. The backend stores only a SHA-256 hash of the token; you can revoke
  any device from **aptly.fyi → Profile → Connected devices**.

## Load unpacked (development)

Almost no-build: the popup (HTML) and background (ESM module worker) load as
authored source. The **content scripts are the one exception** — MV3 content
scripts are classic scripts and can't use ESM `import`, so `npm run build`
flattens each entry + its imports (`shared.js`, `config.js`) into a single
import-free IIFE: `src/content/greenhouse.js → content/greenhouse.js`
(Greenhouse/Lever/Ashby/SmartRecruiters) and `src/content/workday.js →
content/workday.js` (experimental Workday). The bundled files are committed, so
the repo loads unpacked from the root with no build step.

1. `cd extension && npm run build` — flattens content scripts + validates the
   manifest. (Run this after editing anything under `src/content/`.)
2. Open `chrome://extensions`, enable **Developer mode**.
3. Click **Load unpacked** and select the `extension/` directory
   (or `extension/dist/` for the zip-ready copy).
4. Pin the Aptly icon to the toolbar.

> Editing `src/content/*`? Re-run `npm run build` and reload the extension —
> the `content/*.js` bundles are generated; don't hand-edit them.

## Connect your account

1. Click the Aptly toolbar icon → **Sign in to Aptly**.
2. A tab opens `https://aptly.fyi/extension/connect`. If you're signed in to
   aptly.fyi, it mints a token and hands it to the extension; otherwise sign in
   first.
3. The popup now shows **Signed in as {name}**.

## Develop against a local backend

Edit `src/lib/config.js` and point `API_BASE` / `WEB_BASE` at your local
servers (e.g. `http://localhost:8000` / `http://localhost:3000`), then add
those origins to `host_permissions` and `externally_connectable` in
`manifest.json`. Reload the unpacked extension.

## Layout

```
extension/
  manifest.json
  src/
    background.js          service worker (auth handoff, badge, QA relay)
    content/
      greenhouse.js        standard-DOM ATSes: Greenhouse/Lever/Ashby/SmartRecruiters
      workday.js           experimental Workday adapter (custom dropdowns)
      shared.js            platform-agnostic field detection/fill helpers
    popup/
      popup.html / popup.js      toolbar UI
      connected.html             fallback token-handoff page
    lib/
      api.js  storage.js  config.js
  public/icons/            16/32/48/128 px icons
  scripts/build.mjs        validate + emit dist/
  test/detection.test.mjs  node test for the field-detection heuristic
  test/workday.test.mjs    node test for the Workday dropdown matcher
```

Run `npm test` (no deps) to exercise the detection heuristic + the Workday
dropdown matcher against DOM stubs; `npm run validate` to check the manifest
references resolve.

## How form detection works (and the ATS DOMs)

Detection is **selector-agnostic** so it survives DOM changes — and works
across ATSes — without per-version selectors:

- It scans the whole document for visible `<input>/<select>/<textarea>`
  (`isApplicationInput` in `shared.js`), excluding chrome (nav/header/footer),
  search boxes, and subscribe/language widgets.
- A page counts as an application form once **≥ 3** such inputs are present.
  When a first `<form>` is too sparse to be the application (e.g. a header
  search/login form on a non-Greenhouse ATS), the scan widens to the whole
  document so fields rendered outside it aren't missed.
- A debounced `MutationObserver` on `document.body` re-detects as the page
  renders, and the popup re-detects fresh on open (`GH_PING`). Console logs are
  prefixed `[Aptly]` for easy debugging.

Because it's selector-agnostic, the **same** content script (`greenhouse.js`)
serves Greenhouse, Lever, Ashby, and SmartRecruiters. Greenhouse alone runs
**two DOM generations**:

- **Old — `boards.greenhouse.io`:** server-rendered, fields inside a real
  `<form id="application_form">`, questions in `<label for=…>`.
- **New — `job-boards.greenhouse.io`:** a React SPA. The application form mounts
  *after* initial load (so one-shot detection misses it) and may **not** wrap
  fields in a `<form>` at all — which is exactly why the old form-centric
  detection failed. The page-wide scan + `MutationObserver` handle both, and
  `all_frames: true` covers forms embedded in iframes on custom company domains.

### Workday (experimental, unverified)

Workday is **not** a standard-DOM form, so it gets its own content script
(`workday.js`, matched only on `*.myworkdayjobs.com`). It reuses the shared
helpers + the `GH_*` message protocol but adds Workday-specific fillers:
fields are discovered by `data-automation-id`; **custom dropdowns** are buttons
that open a `[role="listbox"]` of `[role="option"]` nodes (not `<select>`), so
it clicks the trigger, waits for the listbox, and clicks the option whose text
matches (`matchOptionByText` in `shared.js`, unit-tested); **date/spinbutton and
file fields are left as "review" (yellow), never guessed**. The popup labels
Workday clearly as experimental. **Its DOM has not been verified against a live
Workday application — test it before relying on it.**

## Hard rules (by design)

- Never auto-submits a form. You submit.
- Never auto-fills demographic questions without explicit opt-in.
- Never modifies the host page beyond filling values + a small status dot.
- No data persisted in the extension beyond the token + prefs.
