// Popup controller (vanilla JS, no build step). Renders one of a few states:
//   disconnected → "Sign in to Aptly"
//   connected, not on Greenhouse → status
//   connected, no done tailor-run → "Tailor a resume first"
//   connected, on Greenhouse → resume picker + "Start filling" → fill summary
import { api, AuthError } from "../lib/api.js";
import { getToken, clearToken } from "../lib/storage.js";
import { CONNECT_URL } from "../lib/config.js";

const root = document.getElementById("root");
const accountEl = document.getElementById("account");

function h(html) {
  root.innerHTML = html;
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function isGreenhouse(url) {
  return /https:\/\/([a-z0-9-]+\.)?(job-boards\.)?greenhouse\.io\//i.test(url || "");
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
  if (!isGreenhouse(tab?.url)) return renderIdle(me);

  // On a Greenhouse page — ask the content script what it sees.
  let info = { hasForm: false, count: 0 };
  try {
    info = await chrome.tabs.sendMessage(tab.id, { type: "GH_PING" });
  } catch (_) {
    // content script not ready on this page
  }
  if (!info?.hasForm) return renderIdle(me, "No application form detected on this page yet.");
  if (!me.has_active_tailor_run) return renderNoResume();

  renderGreenhouse(me, tab, info);
}

function renderDisconnected(note) {
  accountEl.textContent = "";
  h(`
    ${note ? `<p class="muted">${note}</p>` : ""}
    <h2>Fill applications faster</h2>
    <p class="muted">Connect your Aptly account to fill Greenhouse application forms with your
      profile and tailored resumes. You review every field and submit yourself.</p>
    <button class="primary" id="signin">Sign in to Aptly</button>
  `);
  document.getElementById("signin").onclick = () => {
    chrome.tabs.create({ url: CONNECT_URL });
    window.close();
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
    <p class="muted">${note || "Open a Greenhouse application page and I'll help you fill it."}</p>
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

async function renderGreenhouse(me, tab, info) {
  let runs = [];
  try {
    runs = await api.tailorRuns();
  } catch (_) {
    /* ignore */
  }
  const options = runs
    .map((r) => `<option value="${r.id}">${r.job_title || "Tailored resume"}</option>`)
    .join("");
  h(`
    <h2>Greenhouse application detected</h2>
    <p class="muted"><b>${info.count}</b> field${info.count === 1 ? "" : "s"} found</p>
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
    <div class="hint">Aptly never submits for you — you click submit on Greenhouse yourself.</div>
    <div id="result"></div>
    ${signoutBar()}
  `);

  document.getElementById("start").onclick = async () => {
    const runId = document.getElementById("run").value;
    const btn = document.getElementById("start");
    btn.disabled = true;
    btn.textContent = "Filling…";
    try {
      const [profile, resume] = await Promise.all([api.profile(), runId ? api.resume(runId) : null]);
      const res = await chrome.tabs.sendMessage(tab.id, {
        type: "GH_FILL",
        payload: { profile, resume, prefs: {} },
      });
      renderSummary(res?.summary, runId);
    } catch (e) {
      document.getElementById("result").innerHTML = `<p class="err">${e?.message || e}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Start filling";
    }
  };
}

function renderSummary(summary, runId) {
  if (!summary) return;
  const el = document.getElementById("result");
  const fileNote =
    summary.file > 0
      ? `<div class="hint">Resume upload can't be auto-attached (a browser security rule).
         <a href="${api.downloadUrl(runId)}" target="_blank">Download your DOCX</a> and drop it
         into the resume field.</div>`
      : "";
  el.innerHTML = `
    <div class="card summary">
      <p style="margin:0 0 6px"><b>${summary.green}</b> from profile ·
         <b>${summary.yellow}</b> to review · <b>${summary.red}</b> need your input</p>
      ${summary.sensitive > 0 ? `<p class="muted" style="margin:0">${summary.sensitive} demographic field(s) left blank (voluntary).</p>` : ""}
      <p class="muted" style="margin:8px 0 0">Review each field on the page, then submit on Greenhouse yourself.</p>
    </div>
    ${fileNote}
  `;
}

render();
