export type Job = {
  id: number;
  source: string;
  external_id: string;
  company: string;
  title: string;
  location: string | null;
  remote: boolean | null;
  employment_type: string | null;
  salary: string | null;
  skills: string[];
  sponsors_visa: boolean | null;
  url: string;
  description: string | null;
  posted_at: string | null;
  source_updated_at: string | null;
};

export const MANUAL_SOURCE = "manual";

export type JobsResponse = {
  jobs: Job[];
  total: number;
  limit: number;
  offset: number;
  window_hours: number;
};

export type JobsQuery = {
  q?: string;
  company?: string;
  location?: string;
  remote?: boolean;
  employment_type?: string;
  sponsors_visa?: boolean;
  limit?: number;
  offset?: number;
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function fetchJobs(query: JobsQuery = {}): Promise<JobsResponse> {
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const res = await fetch(`${API_URL}/api/jobs?${params.toString()}`, {
    cache: "no-store",
    // Every call to our backend rides the session cookie. The
    // public endpoints don't NEED it today, but consistent
    // `credentials: 'include'` everywhere makes auditing simple —
    // one rule, no per-endpoint exceptions — and lets per-user
    // filters slot in later without revisiting each fetch site.
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`);
  }
  return (await res.json()) as JobsResponse;
}

export async function fetchJob(id: number): Promise<Job | null> {
  const res = await fetch(`${API_URL}/api/jobs/${id}`, {
    cache: "no-store",
    credentials: "include",
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`Backend returned ${res.status}`);
  return (await res.json()) as Job;
}

export type Health = {
  status: string;
  environment: string;
  database: string;
};

export async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_URL}/api/health`, {
      cache: "no-store",
      credentials: "include",
    });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

// ── Profile editor (Phase 2 preview, single-user) ──────────────────────────

export type ProfileLinks = {
  linkedin?: string | null;
  github?: string | null;
};

export type ProfileExperience = {
  company: string;
  title: string;
  location?: string | null;
  start: string;
  end: string;
  bullets: string[];
};

export type ProfileEducation = {
  school: string;
  degree: string;
  location?: string | null;
  graduation: string;
};

export type Profile = {
  name: string;
  headline?: string | null;
  email?: string | null;
  phone?: string | null;
  location?: string | null;
  links: ProfileLinks;
  summary: string;
  skills: string[];
  experience: ProfileExperience[];
  education: ProfileEducation[];
};

// All user-facing endpoints carry the session cookie via
// `credentials: 'include'`. The backend's SessionMiddleware reads
// `user_id` from the signed cookie; no admin-token header is sent
// from these helpers anymore.

export async function fetchProfile(): Promise<Profile> {
  const res = await fetch(`${API_URL}/api/profile`, {
    credentials: "include",
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as Profile;
}

export async function saveProfile(profile: Profile): Promise<Profile> {
  const res = await fetch(`${API_URL}/api/profile`, {
    method: "PUT",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(profile),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as Profile;
}

// Parse runs as a background job on the backend:
//   POST /api/admin/profile/parse  → 202 { run_id, status_url }
//   GET  /api/admin/profile/parse/{run_id}  → { status, profile?, error? }
//
// The parser is now a deterministic Python pass — milliseconds. The
// background-job + polling shape stays so this client code path is
// unchanged from when the backend used Anthropic, and so any future
// heavy parsing (e.g. PDF upload) can slot in without revisiting the
// API contract.

export const PARSE_POLL_INTERVAL_MS = 2_000;
// Worst-case wait. The parser itself takes milliseconds; this ceiling
// only fires if the worker thread is genuinely stuck (DB down, etc.),
// in which case showing the user a "still working…" message
// indefinitely is worse than surfacing `ParseTimeoutError` and letting
// them retry.
export const PARSE_MAX_WAIT_MS = 30_000;

export type ParseRunStatus = "pending" | "running" | "success" | "failed";

export type ParseRun = {
  run_id: string;
  status: ParseRunStatus;
  profile: Profile | null;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export class ParseTimeoutError extends Error {
  constructor() {
    super(
      "Parse is taking longer than expected. Reload and try again.",
    );
    this.name = "ParseTimeoutError";
  }
}

/** Throws when the parse completed but didn't extract anything we
 * could use — the frontend distinguishes this from a hard error and
 * shows a "couldn't extract details, please fill in manually"
 * message instead of an error toast. */
export class ParseEmptyResultError extends Error {
  constructor() {
    super(
      "We couldn't extract details from that text — please fill the form below manually.",
    );
    this.name = "ParseEmptyResultError";
  }
}

/** Kick off the parse. Returns the run_id the caller can poll. */
export async function startParse(text: string): Promise<{ run_id: string }> {
  const res = await fetch(`${API_URL}/api/profile/parse`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
  const body = (await res.json()) as { run_id: string };
  return body;
}

/** Fetch the current state of a parse run. */
export async function fetchParseRun(run_id: string): Promise<ParseRun> {
  const res = await fetch(
    `${API_URL}/api/profile/parse/${encodeURIComponent(run_id)}`,
    { credentials: "include" },
  );
  if (!res.ok) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
  return (await res.json()) as ParseRun;
}

/**
 * Kick off a parse and poll until it lands at `success` or `failed`.
 * Returns the parsed Profile on success.
 *
 * Throws:
 *   * `ParseEmptyResultError` when the parser ran successfully but
 *     extracted no usable fields (random text, blank input). Callers
 *     should treat this as a soft case — show a friendly "please fill
 *     in manually" message rather than a red error.
 *   * `ParseTimeoutError` if the polling loop hits the ceiling (only
 *     fires when the worker is genuinely stuck — milliseconds in the
 *     happy case).
 *   * Plain `Error` on any other failure (HTTP error, backend `status
 *     === 'failed'`).
 */
export async function parseProfileText(
  text: string,
  opts: { signal?: AbortSignal; onProgress?: (status: ParseRunStatus) => void } = {},
): Promise<Profile> {
  const { signal, onProgress } = opts;
  const { run_id } = await startParse(text);
  onProgress?.("running");

  const deadline = Date.now() + PARSE_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    if (signal?.aborted) {
      throw new DOMException("Parse polling aborted", "AbortError");
    }
    const run = await fetchParseRun(run_id);
    if (run.status === "success") {
      if (!run.profile) {
        // Defence-in-depth: a `success` row without a profile means
        // the backend bug-out path elided the payload. Don't crash —
        // surface a generic message.
        throw new Error("Parse succeeded but no profile was returned. Retry.");
      }
      if (isEmptyProfile(run.profile)) {
        throw new ParseEmptyResultError();
      }
      return run.profile;
    }
    if (run.status === "failed") {
      throw new Error(run.error || "Parse failed");
    }
    await sleep(PARSE_POLL_INTERVAL_MS);
  }
  throw new ParseTimeoutError();
}

/** A Profile counts as "empty" when the deterministic parser couldn't
 * recover ANY meaningful field — no name, no email, no experience, no
 * education, no skills. This is the signal the caller uses to switch
 * from "show the autofilled form" to "show 'please fill in manually'
 * messaging". An empty Profile is NOT an error case — the user just
 * pasted text that didn't look like a resume. */
function isEmptyProfile(p: Profile): boolean {
  return (
    !p.name.trim() &&
    !p.email &&
    !p.phone &&
    (p.skills?.length ?? 0) === 0 &&
    (p.experience?.length ?? 0) === 0 &&
    (p.education?.length ?? 0) === 0
  );
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export type ManualJobInput = {
  title: string;
  company: string;
  apply_url: string;
  location?: string;
  remote?: boolean | null;
  employment_type?: string;
  salary?: string;
  skills?: string[];
  sponsors_visa?: boolean | null;
  description?: string;
};

export async function createManualJob(
  input: ManualJobInput,
  token: string,
): Promise<Job> {
  const res = await fetch(`${API_URL}/api/admin/jobs`, {
    method: "POST",
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": token,
    },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return (await res.json()) as Job;
}

export async function deleteManualJob(id: number, token: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/admin/jobs/${id}`, {
    method: "DELETE",
    credentials: "include",
    headers: { "X-Admin-Token": token },
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail || `Request failed (${res.status})`);
  }
}

async function safeDetail(res: Response): Promise<string | null> {
  try {
    const body = (await res.json()) as { detail?: string };
    return body.detail ?? null;
  } catch {
    return null;
  }
}

// ── Tailoring (Phase 4) ─────────────────────────────────────────────────────

export type Analysis = {
  match_score: number;
  top_skills: string[];
  matched: string[];
  gaps: string[];
  questions: string[];
  /** Step 5 of the ATS spec — JD requirements the candidate genuinely
   * lacks and cannot plausibly confirm via a question. Surfaced
   * honestly instead of being asked about. Optional for backward
   * compatibility with cached analyses created before this field
   * landed (the backend stores Analysis.model_dump() in the cache). */
  genuine_lacks?: string[];
};

export type ExperienceBullet = {
  company: string;
  title: string;
  location: string | null;
  dates: string;
  bullets: string[];
};

export type TailoredResume = {
  summary: string;
  skills: string[];
  experience: ExperienceBullet[];
  education: string[];
  ats_notes: string;
};

export type AnalyzeResponse = {
  job_id: number;
  demo_mode: boolean;
  analysis: Analysis;
};

export type GenerateResponse = {
  job_id: number;
  demo_mode: boolean;
  resume: TailoredResume;
};

export async function analyzeJob(jobId: number): Promise<AnalyzeResponse> {
  const res = await fetch(`${API_URL}/api/tailor/analyze`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as AnalyzeResponse;
}

export async function generateTailoredResume(
  jobId: number,
  answers: Record<string, string>,
): Promise<GenerateResponse> {
  const res = await fetch(`${API_URL}/api/tailor/generate`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, answers }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as GenerateResponse;
}

export async function downloadResumeDocx(
  resume: TailoredResume,
  filename: string,
): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/tailor/docx`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume, filename }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return await res.blob();
}


// ── Auth (Google sign-in) ────────────────────────────────────────────────

export type CurrentUser = {
  id: number;
  email: string;
  name: string | null;
};

/** Fetch the current user, or null when not signed in. Never
 * throws on the 401 path — we treat "no session" as a normal,
 * expected state for the public job list / sign-in page. */
export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  const res = await fetch(`${API_URL}/api/auth/me`, {
    credentials: "include",
    cache: "no-store",
  });
  if (res.status === 401) return null;
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as CurrentUser;
}

/** Server-side endpoint URL the sign-in button can link to. Encoded
 * with the post-login bounce path so the user comes back to where
 * they clicked sign-in from. */
export function googleSignInUrl(next: string = "/"): string {
  const params = new URLSearchParams({ next });
  return `${API_URL}/api/auth/google/login?${params.toString()}`;
}

export async function signOut(): Promise<void> {
  const res = await fetch(`${API_URL}/api/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
}
