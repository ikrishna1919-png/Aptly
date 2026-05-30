// Central config. The extension talks only to the production API; the connect
// flow lives on the marketing origin. Kept in one place so a fork pointing at
// a local backend only edits here.
export const API_BASE = "https://api.aptly.fyi";
export const WEB_BASE = "https://aptly.fyi";
export const CONNECT_URL = `${WEB_BASE}/extension/connect`;

// Greenhouse demographic / sensitive fields are NEVER auto-filled unless the
// user opts in. Matched against the lowercased question text.
export const SENSITIVE_PATTERNS = [
  "race",
  "ethnicity",
  "gender",
  "veteran",
  "disability",
  "sexual orientation",
  "transgender",
];
export const SALARY_PATTERNS = ["salary", "compensation", "pay expectation", "desired pay"];
