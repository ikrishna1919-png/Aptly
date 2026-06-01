// Popup controller (vanilla JS, no build step). Renders one of a few states:
//   disconnected → "Sign in to Aptly"
//   connected, not on a supported ATS → status
//   connected, no done tailor-run → "Tailor a resume first"
//   connected, on a supported ATS → resume picker + "Start filling" → summary
import { api, AuthError } from "../lib/api.js";
import { getToken, setToken, clearToken } from "../lib/storage.js";
import { CONNECT_URL } from "../lib/config.js";

const root = document.getElementById("root");
const accountEl = document.getElementById("account");

function h(html) {
  root.innerHTML = html;
}

// Escape before interpolating into innerHTML. ESSENTIAL for the job-description
// text, which is untrusted third-party page content; also applied to profile
// fields defensively.
function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

// Hosts handled by the standard-DOM content script (content/greenhouse.js).
const ATS_HOST_PATTERNS = [
  /https:\/\/([a-z0-9-]+\.)?(job-boards\.)?greenhouse\.io\//i, // Greenhouse
  /https:\/\/jobs\.lever\.co\//i, // Lever
  /https:\/\/([a-z0-9-]+\.)?ashbyhq\.com\//i, // Ashby
  /https:\/\/(jobs|careers)\.smartrecruiters\.com\//i, // SmartRecruiters posting
  /https:\/\/([a-z0-9-]+\.)?smartrecruiterscareers\.com\//i, // SmartRecruiters apply workflow
];

// Workday is handled by the separate, experimental content/workday.js and is
// labelled distinctly in the UI. The host is usually multi-segment
// ({tenant}.wd1.myworkdayjobs.com, wd5, …), so match one-or-more subdomains.
function isWorkday(url) {
  return /https:\/\/([a-z0-9-]+\.)+myworkdayjobs\.com\//i.test(url || "");
}

function isSupportedAts(url) {
  const u = url || "";
  return ATS_HOST_PATTERNS.some((re) => re.test(u)) || isWorkday(u);
}

async function render() {
  const token = await getToken();
  if (!token) return renderDisconnected();

  let me;
  try {
    me = await api.me();
  } catch (e) {
    if (e instanceof AuthError) return renderDisconnected("Your session expired.");
    return renderError(e);
  }
  accountEl.textContent = me.name || me.email || "";

  const tab = await activeTab();
  if (!isSupportedAts(tab?.url)) return renderIdle(me);

  // On a supported ATS page — ask the content script what it sees.
  let info = { hasForm: false, count: 0 };
  try {
    info = await chrome.tabs.sendMessage(tab.id, { type: "GH_PING" });
  } catch (_) {
    // content script not ready on this page
  }
  if (!info?.hasForm) return renderIdle(me, "No application form detected on this page yet.");
  if (!me.has_active_tailor_run) return renderNoResume();

  renderAts(me, tab, info, isWorkday(tab?.url));
}

function renderDisconnected(note) {
  accountEl.textContent = "";
  h(`
    ${note ? `<p class="muted">${note}</p>` : ""}
    <h2>Connect to Aptly</h2>
    <p class="muted">Connect your Aptly account to fill job application forms with your
      profile and tailored resumes. You review every field and submit yourself.</p>
    <button class="primary" id="signin">Sign in with your Aptly account</button>

    <div class="divider"><span>or paste a code</span></div>

    <label class="muted" for="code">Have a connection code?</label>
    <input id="code" class="code-input" type="text" placeholder="Paste your code" autocomplete="off" spellcheck="false" />
    <button class="secondary" id="connect" disabled>Connect</button>
    <p class="err" id="code-err" style="display:none"></p>
  `);

  // Primary path: open the connect page WITH this extension's id so the page
  // can hand the token straight back via postMessage (externally_connectable).
  document.getElementById("signin").onclick = () => {
    const url = `${CONNECT_URL}?ext_id=${encodeURIComponent(chrome.runtime.id)}`;
    chrome.tabs.create({ url });
    // Don't close — leave the popup open so the TOKEN_RECEIVED message can
    // auto-advance it. (User can also close it; reopening shows signed-in.)
  };

  // Fallback path: paste a code minted by the connect page.
  const input = document.getElementById("code");
  const connectBtn = document.getElementById("connect");
  const errEl = document.getElementById("code-err");
  input.addEventListener("input", () => {
    connectBtn.disabled = input.value.trim().length === 0;
    errEl.style.display = "none";
  });
  connectBtn.onclick = async () => {
    const code = input.value.trim();
    if (!code) return;
    connectBtn.disabled = true;
    connectBtn.textContent = "Connecting…";
    errEl.style.display = "none";
    // Validate by storing the token then probing /me. On failure we roll the
    // token back so a bad paste can't leave a broken half-connected state.
    await setToken(code);
    try {
      await api.me();
      render(); // success → re-render into signed-in state
    } catch (_) {
      await clearToken();
      errEl.textContent = "That code didn't work. Try the Sign in button above.";
      errEl.style.display = "block";
      connectBtn.disabled = false;
      connectBtn.textContent = "Connect";
      // Per spec: don't clear the input.
    }
  };
}

function renderError(e) {
  h(`<p class="err">${e?.message || e}</p>
     <button class="link" id="retry">Try again</button>`);
  document.getElementById("retry").onclick = render;
}

function signoutBar() {
  setTimeout(() => {
    const el = document.getElementById("signout");
    if (el)
      el.onclick = async () => {
        await clearToken();
        render();
      };
  }, 0);
  return `<button class="link" id="signout" style="margin-top:12px">Sign out</button>`;
}

function renderIdle(me, note) {
  h(`
    <h2>Signed in as ${me.name || me.email}</h2>
    <p class="muted">${note || "Open a job application on a supported site (Greenhouse, Lever, Ashby, SmartRecruiters) and I'll help you fill it."}</p>
    ${signoutBar()}
  `);
}

function renderNoResume() {
  h(`
    <h2>Tailor a resume first</h2>
    <p class="muted">You don't have a finished tailored resume yet. Create one at
      <a href="https://aptly.fyi/ats" target="_blank">aptly.fyi/ats</a>, then come back here.</p>
    ${signoutBar()}
  `);
}

async function renderAts(me, tab, info, experimental) {
  let runs = [];
  try {
    runs = await api.tailorRuns();
  } catch (_) {
    /* ignore */
  }
  // Pre-select the run the user marked "Add to Chrome extension" on the web
  // app (their active autofill resume), if it's still in the recent list.
  const activeId = me.active_autofill_run_id || "";
  const options = runs
    .map(
      (r) =>
        `<option value="${r.id}"${r.id === activeId ? " selected" : ""}>${
          r.job_title || "Tailored resume"
        }${r.id === activeId ? " (your autofill resume)" : ""}</option>`,
    )
    .join("");
  // Workday support is unverified — surface that prominently so the user knows
  // to scrutinise every field, not just trust the dots.
  const heading = experimental ? "Workday form detected" : "Application form detected";
  const experimentalNote = experimental
    ? `<div class="hint" style="background:#fef9c3;border-color:#fde68a">
         <b>Experimental.</b> Workday support is new and unverified — review every
         field carefully before you submit.</div>`
    : "";
  h(`
    <h2>${heading}</h2>
    <p class="muted"><b>${info.count}</b> field${info.count === 1 ? "" : "s"} found</p>
    ${experimentalNote}
    <div id="review"><p class="muted" style="margin:6px 0 0">Loading review…</p></div>
    <div class="card">
      <label class="muted" for="run">Use which tailored resume?</label>
      <select id="run">${options || `<option value="">(none)</option>`}</select>
      <button class="primary" id="start" style="margin-top:12px"${runs.length ? "" : " disabled"}>
        Start filling
      </button>
      <div class="legend">
        <span><i class="dot" style="background:#16a34a"></i> from profile</span>
        <span><i class="dot" style="background:#eab308"></i> review</span>
        <span><i class="dot" style="background:#dc2626"></i> your input</span>
      </div>
    </div>
    <div class="hint">Aptly never submits for you — you click submit yourself.</div>
    <div id="result"></div>
    ${signoutBar()}
  `);

  document.getElementById("start").onclick = async () => {
    const runId = document.getElementById("run").value;
    const btn = document.getElementById("start");
    btn.disabled = true;
    btn.textContent = "Filling…";
    try {
      const profile = await api.profile();
      // runId (not the resume body) goes to the content script; it asks the
      // service worker to fetch + attach the DOCX so the token stays out of the
      // page world.
      const res = await chrome.tabs.sendMessage(tab.id, {
        type: "GH_FILL",
        payload: { profile, runId, prefs: {} },
      });
      renderSummary(res?.summary, runId);
    } catch (e) {
      document.getElementById("result").innerHTML = `<p class="err">${e?.message || e}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Start filling";
    }
  };

  // Foundation review panel: parsed JD + saved profile, fetched async so the
  // form card renders immediately. Each half fails independently (no throwing).
  loadReview(tab);
}

// Build the read-only review shown above "Start filling": the job description
// parsed from the page (GET_PAGE_CONTEXT) and the user's saved Aptly profile
// (existing authenticated endpoint). Both halves degrade gracefully.
async function loadReview(tab) {
  const el = document.getElementById("review");
  if (!el) return;

  const [ctxRes, profRes] = await Promise.allSettled([
    chrome.tabs.sendMessage(tab.id, { type: "GET_PAGE_CONTEXT" }),
    api.profile(),
  ]);

  const ctx = ctxRes.status === "fulfilled" ? ctxRes.value : null;
  const jd = ctx && typeof ctx.jdText === "string" ? ctx.jdText.trim() : "";
  const jdBody = jd
    ? `<div class="jd-text">${escapeHtml(jd)}</div>`
    : `<p class="muted" style="margin:6px 0 0">Couldn't read a job description on this page.</p>`;

  const profileBody =
    profRes.status === "fulfilled"
      ? renderProfileSummary(profRes.value)
      : `<p class="muted" style="margin:6px 0 0">Couldn't load your profile.
         <button class="link" id="review-retry">Retry</button></p>`;

  el.innerHTML = `
    <details open class="review-detail">
      <summary>Job description</summary>
      ${jdBody}
    </details>
    <details open class="review-detail">
      <summary>Your Aptly profile</summary>
      ${profileBody}
    </details>
  `;

  const retry = document.getElementById("review-retry");
  if (retry) retry.onclick = () => loadReview(tab);
}

function renderProfileSummary(p) {
  if (!p) return `<p class="muted" style="margin:6px 0 0">No profile data.</p>`;
  const rows = [];
  if (p.name) rows.push(`<b>${escapeHtml(p.name)}</b>`);
  const role = [p.current_title, p.current_company].filter(Boolean).map(escapeHtml).join(" · ");
  if (role) rows.push(role);
  const contact = [p.email, p.phone, p.location].filter(Boolean).map(escapeHtml).join(" · ");
  if (contact) rows.push(`<span class="muted">${contact}</span>`);
  const links = [
    p.linkedin && "LinkedIn",
    p.github && "GitHub",
    p.portfolio && "Portfolio",
  ]
    .filter(Boolean)
    .join(" · ");
  if (links) rows.push(`<span class="muted">${links}</span>`);
  if (p.work_auth_status) {
    rows.push(`<span class="muted">Work authorization: ${escapeHtml(p.work_auth_status)}</span>`);
  }
  if (!rows.length) {
    return `<p class="muted" style="margin:6px 0 0">Your profile looks empty — add details at
      <a href="https://aptly.fyi" target="_blank">aptly.fyi</a>.</p>`;
  }
  return `<div class="profile-summary">${rows.map((r) => `<div>${r}</div>`).join("")}</div>`;
}

function renderSummary(summary, runId) {
  if (!summary) return;
  const el = document.getElementById("result");
  const attachedNote =
    summary.fileAttached > 0
      ? `<div class="hint" style="background:#dcfce7;border-color:#bbf7d0">Resume attached${
          summary.attachedName ? `: <b>${summary.attachedName}</b>` : ""
        }. Confirm it on the page before submitting.</div>`
      : "";
  const fileNote =
    summary.file > 0
      ? `<div class="hint">Couldn't auto-attach the resume to ${summary.file} field(s)
         (a browser security rule can block it). <a href="${api.downloadUrl(runId)}" target="_blank">Download
         your DOCX</a> and drop it into the resume field.</div>`
      : "";
  el.innerHTML = `
    <div class="card summary">
      <p style="margin:0 0 6px"><b>${summary.green}</b> from profile ·
         <b>${summary.yellow}</b> to review · <b>${summary.red}</b> need your input</p>
      ${summary.sensitive > 0 ? `<p class="muted" style="margin:0">${summary.sensitive} demographic field(s) left blank (voluntary).</p>` : ""}
      <p class="muted" style="margin:8px 0 0">Review each field on the page, then submit it yourself.</p>
    </div>
    ${attachedNote}
    ${fileNote}
  `;
}

// Auto-advance when the connect page hands the token to the service worker via
// postMessage (the primary flow). Background broadcasts TOKEN_RECEIVED once
// it has stored the token; if this popup is still open, re-render into the
// signed-in state.
chrome.runtime.onMessage.addListener((msg) => {
  if (msg && msg.type === "TOKEN_RECEIVED") render();
});

render();
