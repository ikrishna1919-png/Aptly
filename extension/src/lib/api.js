// Aptly backend client. Every call carries the bearer token (NOT a cookie —
// chrome-extension:// can't ride the first-party cookie, and we deliberately
// don't add the extension origin to CORS). A 401 means the session was revoked
// or expired → callers clear the token and prompt re-connect.
import { API_BASE } from "./config.js";
import { getToken, clearToken } from "./storage.js";

export class AuthError extends Error {}

async function call(path, { method = "GET", body, raw = false } = {}) {
  const token = await getToken();
  if (!token) throw new AuthError("Not connected");
  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    await clearToken();
    throw new AuthError("Session expired");
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const j = await res.json();
      if (j && j.detail) detail = typeof j.detail === "string" ? j.detail : detail;
    } catch (_) {
      /* ignore */
    }
    throw new Error(detail);
  }
  return raw ? res : res.json();
}

export const api = {
  me: () => call("/api/extension/me"),
  profile: () => call("/api/extension/profile"),
  tailorRuns: () => call("/api/extension/tailor-runs?limit=5"),
  resume: (runId) => call(`/api/extension/tailor-runs/${encodeURIComponent(runId)}/resume`),
  downloadUrl: (runId) =>
    `${API_BASE}/api/extension/tailor-runs/${encodeURIComponent(runId)}/download`,
  qaLookup: (question_text, field_type, options) =>
    call("/api/extension/qa/lookup", {
      method: "POST",
      body: { question_text, field_type, options },
    }),
  qaSave: (payload) => call("/api/extension/qa/save", { method: "POST", body: payload }),
  applicationsSubmitted: (payload) =>
    call("/api/extension/applications-submitted", { method: "POST", body: payload }),
};
