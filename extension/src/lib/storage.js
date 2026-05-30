// Thin async wrapper over chrome.storage.local. We persist ONLY the bearer
// token + lightweight preferences here — never profile data, resume content,
// or saved answers (those live in the backend and are fetched per-session).
const TOKEN_KEY = "aptly_token";
const PREFS_KEY = "aptly_prefs";

const DEFAULT_PREFS = {
  rememberDemographics: false, // sensitive fields: explicit opt-in only
  shareAnalytics: false, // applications-submitted ping: default OFF
};

export async function getToken() {
  const v = await chrome.storage.local.get(TOKEN_KEY);
  return v[TOKEN_KEY] || null;
}

export async function setToken(token) {
  await chrome.storage.local.set({ [TOKEN_KEY]: token });
}

export async function clearToken() {
  await chrome.storage.local.remove(TOKEN_KEY);
}

export async function getPrefs() {
  const v = await chrome.storage.local.get(PREFS_KEY);
  return { ...DEFAULT_PREFS, ...(v[PREFS_KEY] || {}) };
}

export async function setPrefs(prefs) {
  const next = { ...(await getPrefs()), ...prefs };
  await chrome.storage.local.set({ [PREFS_KEY]: next });
  return next;
}
