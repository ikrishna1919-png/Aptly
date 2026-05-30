# Aptly Application Assistant — Chrome Extension (v1.0)

A Manifest V3 browser extension that helps you fill **Greenhouse** job
application forms with your Aptly profile and tailored resumes. **You review
every field and click submit yourself** — the extension never submits for you.

> v1.0 supports Greenhouse only. Lever, Ashby, and Workday adapters are
> planned as separate phased releases after v1.0 is validated with real usage.

## What it does

- Detects a Greenhouse application form and shows the field count on the
  toolbar badge.
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
authored source. The **content script is the one exception** — MV3 content
scripts are classic scripts and can't use ESM `import`, so `npm run build`
flattens `src/content/greenhouse.js` + its imports (`shared.js`, `config.js`)
into a single import-free IIFE at `content/greenhouse.js` (the path the
manifest references). The bundled file is committed, so the repo loads
unpacked from the root with no build step.

1. `cd extension && npm run build` — flattens content scripts + validates the
   manifest. (Run this after editing anything under `src/content/`.)
2. Open `chrome://extensions`, enable **Developer mode**.
3. Click **Load unpacked** and select the `extension/` directory
   (or `extension/dist/` for the zip-ready copy).
4. Pin the Aptly icon to the toolbar.

> Editing `src/content/*`? Re-run `npm run build` and reload the extension —
> `content/greenhouse.js` is generated; don't hand-edit it.

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
      greenhouse.js        Greenhouse adapter (detect + fill + colour-code)
      shared.js            platform-agnostic field detection/fill helpers
    popup/
      popup.html / popup.js      toolbar UI
      connected.html             fallback token-handoff page
    lib/
      api.js  storage.js  config.js
  public/icons/            16/32/48/128 px icons
  scripts/build.mjs        validate + emit dist/
  test/detection.test.mjs  node test for the field-detection heuristic
```

Run `npm test` (no deps) to exercise the detection heuristic against a DOM
stub; `npm run validate` to check the manifest references resolve.

## How form detection works (and the Greenhouse DOM)

Detection is **selector-agnostic** so it survives Greenhouse DOM changes
without per-version selectors:

- It scans the whole document for visible `<input>/<select>/<textarea>`
  (`isApplicationInput` in `shared.js`), excluding chrome (nav/header/footer),
  search boxes, and subscribe/language widgets.
- A page counts as an application form once **≥ 3** such inputs are present.
- A debounced `MutationObserver` on `document.body` re-detects as the page
  renders, and the popup re-detects fresh on open (`GH_PING`). Console logs are
  prefixed `[Aptly]` for easy debugging.

Greenhouse runs **two DOM generations**:

- **Old — `boards.greenhouse.io`:** server-rendered, fields inside a real
  `<form id="application_form">`, questions in `<label for=…>`.
- **New — `job-boards.greenhouse.io`:** a React SPA. The application form mounts
  *after* initial load (so one-shot detection misses it) and may **not** wrap
  fields in a `<form>` at all — which is exactly why the old form-centric
  detection failed. The page-wide scan + `MutationObserver` handle both, and
  `all_frames: true` covers forms embedded in iframes on custom company domains.

## Hard rules (by design)

- Never auto-submits a form. You submit.
- Never auto-fills demographic questions without explicit opt-in.
- Never modifies the host page beyond filling values + a small status dot.
- No data persisted in the extension beyond the token + prefs.
