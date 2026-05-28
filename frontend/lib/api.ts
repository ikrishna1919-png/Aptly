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
  // ── Sponsorship intelligence (DOL H-1B LCA) ────────────────────────────
  // `sponsors_h1b` is the conservative signal (≥N LCAs in the past
  // 12 months); `past_h1b_activity` is the inclusive one (any LCA in
  // the past 3 years). Both default to `false` when the backend has
  // no row for the employer — a `false` is NOT a claim that the
  // company doesn't sponsor, and the UI must never render it as a
  // negative badge.
  sponsors_h1b: boolean;
  past_h1b_activity: boolean;
  lca_count_12mo: number;
  lca_count_3yr: number;
  most_recent_lca_filing: string | null;
};

export const MANUAL_SOURCE = "manual";

export type JobsResponse = {
  jobs: Job[];
  total: number;
  limit: number;
  offset: number;
  // Backend-derived page math — present since the pagination work
  // landed alongside the jobs-page redesign. `page` is 1-indexed;
  // `total_pages` is 0 when `total` is 0.
  page: number;
  total_pages: number;
  window_hours: number;
};

export type JobsQuery = {
  q?: string;
  company?: string;
  location?: string;
  remote?: boolean;
  employment_type?: string;
  sponsors_visa?: boolean;
  /** Conservative H-1B signal: only jobs at employers with ≥ 5 LCAs
   * filed in the past 12 months. `false` is not a meaningful filter
   * value — silence in the DOL data isn't evidence the company
   * doesn't sponsor — and the backend treats it as 'no filter'. */
  sponsors_h1b?: boolean;
  /** Inclusive H-1B signal: jobs at employers with any LCA filed in
   * the past 3 years. Same `false`-is-no-filter contract as above. */
  past_h1b_activity?: boolean;
  limit?: number;
  offset?: number;
};

// Empty-string base = relative URLs. Every browser → backend call
// goes through Next.js' same-origin proxy (configured in
// `next.config.mjs`), which forwards `/api/*` to the real backend
// over the Vercel edge. This makes the session cookie FIRST-PARTY
// for the frontend origin, which is what's needed for Safari /
// incognito sign-in to work (third-party cookies are blocked
// there).
//
// `NEXT_PUBLIC_API_URL` is still honoured for backward
// compatibility with any tooling that sets it (e.g. a Playwright
// suite pointing at a custom backend) — when set it's used as the
// absolute base. Production deploys should leave it unset so the
// proxy path is exercised.
const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

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
  /** Personal site / portfolio (NOT LinkedIn or GitHub). Surfaced as
   * a third link slot alongside the social profiles. */
  website?: string | null;
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
  /** Major / field of study. Lives separately from `degree` so the
   * UI can edit the credential ("B.S.") and the major ("Computer
   * Science") independently. Legacy rows that stored the major
   * inside `degree` still render — the parser splits new rows. */
  field_of_study?: string | null;
  location?: string | null;
  /** Canonical attendance dates. `start` is enrolment, `end` is
   * graduation (or "Present"). The legacy `graduation` field on
   * the backend mirrors `end` so old code still works. */
  start?: string;
  end?: string;
  graduation: string;
  /** Self-reported GPA when present on the resume ("3.85/4.0",
   * "3.85"). Null when the resume doesn't surface one. */
  gpa?: string | null;
  /** "Relevant Coursework:" list when the resume includes one.
   * Empty when the source has no coursework block. */
  coursework?: string[];
};

/** One row of categorised skills (`Cloud Platforms: AWS, Azure`).
 * The Profile's `skills` field can be either a flat list of strings
 * (legacy / ungrouped resumes) OR a list of these groups (new
 * categorised resumes). Frontend dispatches on element shape. */
export type ProfileSkillGroup = {
  category: string | null;
  items: string[];
};

export type ProfileProject = {
  name: string;
  description: string;
  /** Bullet list of achievements / features. Lives alongside
   * `description`; older resumes use one paragraph, newer ones use
   * a bullet list under the project name. Both can coexist. */
  bullets?: string[];
  technologies: string[];
  link?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

export type ProfileAchievement = {
  title: string;
  description: string;
  date?: string | null;
};

/** Distinct from `ProfileAchievement`: certifications are named
 * credentials with an issuer (AWS, PMI, Microsoft, etc.) and
 * sometimes a credential ID. Backend parser keeps them separate
 * to fix a misclassification where every credential ended up in
 * achievements. */
export type ProfileCertification = {
  name: string;
  issuer?: string | null;
  date?: string | null;
  credential_id?: string | null;
};

/** Spoken / written natural language. NOT for programming languages
 * — those live in `skills`. */
export type ProfileLanguage = {
  name: string;
  proficiency?: string | null;
};

/** Volunteer / community-service experience. Same shape as a paid
 * job but lives in its own section so the user's actual employment
 * history stays clean. */
export type ProfileVolunteer = {
  organization: string;
  role?: string | null;
  description: string;
  location?: string | null;
  start_date?: string | null;
  end_date?: string | null;
  bullets: string[];
};

export type ProfilePublication = {
  title: string;
  venue?: string | null;
  date?: string | null;
  link?: string | null;
  /** Free-form author string — preserves the order + et-al
   * formatting the candidate used on the resume. */
  authors?: string | null;
};

export type ProfileAffiliation = {
  name: string;
  role?: string | null;
  date?: string | null;
};

/** Catch-all for unrecognised section headings. Keeps unusual
 * resume content (Hobbies, Patents, Conference Talks, etc.) editable
 * rather than silently dropped. */
export type ProfileAdditionalSection = {
  label: string;
  content: string;
};

export type Profile = {
  name: string;
  headline?: string | null;
  /** True when the headline was derived by the parser (most recent
   * role + years of experience) rather than pulled verbatim from
   * the resume. The UI marks an inferred headline so the user knows
   * to confirm or edit it. */
  headline_inferred?: boolean;
  email?: string | null;
  phone?: string | null;
  location?: string | null;
  links: ProfileLinks;
  summary: string;
  /** Either a flat list of strings (legacy / ungrouped) OR a list
   * of `{category, items}` groups (new categorised shape). UI code
   * dispatches on element type. */
  skills: string[] | ProfileSkillGroup[];
  experience: ProfileExperience[];
  education: ProfileEducation[];
  projects: ProfileProject[];
  achievements: ProfileAchievement[];
  certifications: ProfileCertification[];
  languages: ProfileLanguage[];
  volunteer: ProfileVolunteer[];
  publications: ProfilePublication[];
  affiliations: ProfileAffiliation[];
  additional_sections: ProfileAdditionalSection[];
  // Order the user's resume presents sections in (lowercase
  // identifiers). The tailor service mirrors this in the generated
  // resume. Defaulted to [] on the backend so the form always sees
  // an array.
  section_order: string[];
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
// Worst-case wait for the polling loop. The backend's hard
// wall-clock ceiling on the Anthropic call is 60s; the worker can
// then take a couple of seconds to write the terminal status. 120s
// gives the worker plenty of headroom while still surfacing a clear
// timeout to the user when the row really is stuck.
export const PARSE_MAX_WAIT_MS = 120_000;
// How long to wait before swapping the spinner copy from "Parsing
// your resume…" to a calmer "still working…" message — gives the
// user a signal that a long parse is normal and not stuck.
export const PARSE_STILL_WORKING_AFTER_MS = 15_000;

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
      "Parse is still running after two minutes — the worker may be stuck. " +
        "Reload the page and try again, and if the problem persists paste " +
        "your resume as text instead.",
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

export const RESUME_UPLOAD_ACCEPT = ".pdf,.docx" as const;
export const RESUME_UPLOAD_MAX_BYTES = 10 * 1024 * 1024;

/** Kick off a parse from a PDF or DOCX upload. The backend extracts
 * the text and then runs the same hybrid parser the paste path
 * uses. Returns the run id for polling.
 *
 * Surfaces friendly error messages for the three expected failure
 * modes:
 *   * Unsupported extension — `.pdf` and `.docx` only (400).
 *   * Empty extract — almost always a scanned / image-only PDF.
 *     The caller turns this into a "paste your text instead"
 *     prompt (422).
 *   * Oversize file — 413.
 */
export async function startParseUpload(file: File): Promise<{ run_id: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/profile/parse/upload`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) {
    throw new Error((await safeDetail(res)) || `Upload failed (${res.status})`);
  }
  return (await res.json()) as { run_id: string };
}

/** Same shape as `parseProfileText` but kicks off from an uploaded
 * file. Polls until success / failed. Throws the same error subclasses
 * so the page's existing error-handling branches work unchanged. */
export async function parseProfileFile(
  file: File,
  opts: { signal?: AbortSignal; onProgress?: (status: ParseRunStatus) => void } = {},
): Promise<Profile> {
  const { signal, onProgress } = opts;
  const { run_id } = await startParseUpload(file);
  onProgress?.("running");

  const deadline = Date.now() + PARSE_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    if (signal?.aborted) {
      throw new DOMException("Parse polling aborted", "AbortError");
    }
    const run = await fetchParseRun(run_id);
    if (run.status === "success") {
      if (!run.profile) {
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
