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

This is a no-bundler extension — it loads as-is.

1. `cd extension && npm run validate` (checks the manifest references resolve).
2. Open `chrome://extensions`, enable **Developer mode**.
3. Click **Load unpacked** and select the `extension/` directory
   (or `extension/dist/` after `npm run build`).
4. Pin the Aptly icon to the toolbar.

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
```

## Hard rules (by design)

- Never auto-submits a form. You submit.
- Never auto-fills demographic questions without explicit opt-in.
- Never modifies the host page beyond filling values + a small status dot.
- No data persisted in the extension beyond the token + prefs.
