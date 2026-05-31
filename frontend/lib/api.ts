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
  // Derived server-side from `remote` + a JD "hybrid" heuristic — no stored
  // column. "remote" | "hybrid" | "onsite" | null.
  work_model: string | null;
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
  /** Job type: "full-time" | "part-time" | "contract" | "internship".
   * Omit for "any". Backend matches `employment_type` with a JD-regex
   * fallback; jobs of indeterminate type are kept (treated as "any"). */
  job_type?: string;
  /** Work model: "remote" | "hybrid" | "onsite". Omit for "any". (Still a
   * supported API filter; the UI now exposes `job_type` instead.) */
  work_model?: string;
  /** Recency window: "24h" | "7d" | "30d". Omit for "any". */
  posted_within?: string;
  limit?: number;
  offset?: number;
};

// Backend base URL.
//
// In production this should be set to the backend subdomain
// (`https://api.aptly.fyi`). Frontend (`aptly.fyi`) and backend
// share the parent domain `aptly.fyi`, so the session cookie is
// issued with `Domain=.aptly.fyi` and travels first-party on every
// cross-subdomain fetch. No proxy required.
//
// In local dev, leave `NEXT_PUBLIC_API_URL` unset. The relative
// `/api/...` paths fall through to Next.js' rewrites (configured in
// `next.config.mjs`), which forward to `http://localhost:8000`.
// Same-origin from the browser's perspective, so the local session
// cookie also works first-party.
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

/** Thrown when the kickoff POST can't reach the backend at all (as
 * opposed to reaching it and getting an HTTP error). The dominant
 * cause in production is Render's free tier: the service sleeps after
 * inactivity and the first request blocks while the container cold-
 * starts (~30s), which an unbounded `fetch` surfaces as a bare
 * `TypeError: Failed to fetch` with no context. We retry these a few
 * times before giving up; this is what the user sees if every attempt
 * fails. */
export class KickoffNetworkError extends Error {
  constructor(cause?: unknown) {
    super(
      "Couldn't reach the server to start the parse — it may be waking " +
        "from sleep. Wait a few seconds and try again, or fill in the " +
        "form below manually.",
    );
    this.name = "KickoffNetworkError";
    if (cause instanceof Error) this.cause = cause;
  }
}

// Per-attempt ceiling on the kickoff POST. Long enough to absorb a
// ~30s Render cold start in a single attempt, short enough that a
// genuinely dead connection doesn't hang the UI indefinitely.
const KICKOFF_ATTEMPT_TIMEOUT_MS = 35_000;
// Total kickoff attempts before surfacing KickoffNetworkError. Three
// covers the common case (server asleep → wakes on attempt 1 or 2)
// without retrying so long that a truly-down backend feels frozen.
const KICKOFF_MAX_ATTEMPTS = 3;
// Linear backoff base between kickoff attempts (×attempt number).
const KICKOFF_RETRY_BACKOFF_MS = 1_500;

/** Run the kickoff POST with a per-attempt timeout and retry on
 * network-level failure (cold start, dropped connection) or our own
 * attempt-timeout. Calls `onWaking` before each retry so the UI can
 * explain the wait. Does NOT retry HTTP error *responses* (4xx/5xx) —
 * those reached the server and are real; the caller handles them.
 *
 * Note: a retry can in theory create a second parse_runs row if the
 * first attempt's request actually landed but its response was lost
 * (e.g. attempt timed out after the server already created the row).
 * That extra row is harmless — the caller only ever polls the run_id
 * from the attempt that returned, and the backend's startup sweep
 * reaps any abandoned `running` row. The far likelier cold-start
 * failure is the connection never establishing, which creates no row
 * at all. */
async function kickoffFetch(
  input: string,
  init: RequestInit,
  onWaking?: () => void,
): Promise<Response> {
  let lastErr: unknown;
  for (let attempt = 1; attempt <= KICKOFF_MAX_ATTEMPTS; attempt++) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), KICKOFF_ATTEMPT_TIMEOUT_MS);
    try {
      return await fetch(input, { ...init, signal: controller.signal });
    } catch (e) {
      lastErr = e;
      if (attempt < KICKOFF_MAX_ATTEMPTS) {
        onWaking?.();
        await sleep(KICKOFF_RETRY_BACKOFF_MS * attempt);
        continue;
      }
    } finally {
      clearTimeout(timer);
    }
  }
  throw new KickoffNetworkError(lastErr);
}

/** Kick off the parse. Returns the run_id the caller can poll. */
export async function startParse(
  text: string,
  onWaking?: () => void,
): Promise<{ run_id: string }> {
  const res = await kickoffFetch(
    `${API_URL}/api/profile/parse`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    },
    onWaking,
  );
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
export async function startParseUpload(
  file: File,
  onWaking?: () => void,
): Promise<{ run_id: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await kickoffFetch(
    `${API_URL}/api/profile/parse/upload`,
    {
      method: "POST",
      credentials: "include",
      body: form,
    },
    onWaking,
  );
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
  opts: {
    signal?: AbortSignal;
    onProgress?: (status: ParseRunStatus) => void;
    onWaking?: () => void;
  } = {},
): Promise<Profile> {
  const { signal, onProgress, onWaking } = opts;
  const { run_id } = await startParseUpload(file, onWaking);
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
  opts: {
    signal?: AbortSignal;
    onProgress?: (status: ParseRunStatus) => void;
    onWaking?: () => void;
  } = {},
): Promise<Profile> {
  const { signal, onProgress, onWaking } = opts;
  const { run_id } = await startParse(text, onWaking);
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

/** Manual-entry admin endpoints. Gated on the session cookie + the
 * caller's email being in the backend's `ADMIN_EMAILS` allowlist —
 * no more `X-Admin-Token` header. Non-admins get 403 from the
 * backend's `require_admin_user` dependency. */
export async function createManualJob(input: ManualJobInput): Promise<Job> {
  const res = await fetch(`${API_URL}/api/admin/jobs`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const detail = await safeDetail(res);
    throw new Error(detail || `Request failed (${res.status})`);
  }
  return (await res.json()) as Job;
}

export async function deleteManualJob(id: number): Promise<void> {
  const res = await fetch(`${API_URL}/api/admin/jobs/${id}`, {
    method: "DELETE",
    credentials: "include",
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

/** Render style for the tailored resume export. */
export type ResumeMode = "visual" | "plain";

/** Header (name + contact) alignment for the export. Orthogonal to mode;
 * body text always stays left-aligned. */
export type HeaderAlignment = "left" | "center" | "right";

export type ContactLink = { label: string; url: string };

export type ResumeContact = {
  name: string;
  headline: string;
  location: string;
  email: string;
  phone: string;
  links: ContactLink[];
};

export type SkillGroup = { category: string; items: string[] };

export type ExperienceEntry = {
  title: string;
  company: string;
  location: string;
  start_date: string;
  end_date: string;
  bullets: string[];
};

export type EducationEntry = {
  degree: string;
  field: string;
  institution: string;
  location: string;
  graduation_date: string;
};

export type ProjectEntry = { name: string; description: string; bullets: string[] };

export type CertificationEntry = { name: string; issuer: string; date: string };

export type ResumeAts = {
  matched_keywords: string[];
  missing_keywords: string[];
  score_estimate: number;
};

export type ResumeMeta = { mode: ResumeMode; pages_estimate: number };

/** The ATS-standard tailored resume (matches the backend `TailoredResume`). */
export type TailoredResume = {
  meta: ResumeMeta;
  contact: ResumeContact;
  summary: string;
  skills: SkillGroup[];
  experience: ExperienceEntry[];
  education: EducationEntry[];
  projects: ProjectEntry[];
  certifications: CertificationEntry[];
  ats: ResumeAts;
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

/** Download the tailored resume as a file. `format` picks the endpoint
 * (.docx or .pdf); `mode` picks the render style ("visual" default or
 * "plain" for maximum ATS compatibility). Both formats carry identical
 * text — only the styling differs. */
export async function downloadResume(
  resume: TailoredResume,
  filename: string,
  format: "docx" | "pdf",
  mode: ResumeMode = "visual",
  headerAlignment: HeaderAlignment = "center",
  // /ats format: when set, overrides `mode` server-side ("modern" | "classic"
  // | "minimal" | "plain" | "custom"). `custom` carries the custom knobs.
  fmt?: string,
  custom?: Record<string, unknown> | null,
): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/tailor/${format}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      resume,
      filename,
      mode,
      header_alignment: headerAlignment,
      fmt: fmt ?? null,
      custom_options: custom ?? null,
    }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return await res.blob();
}

// ── Run-based tailoring (background + streaming + polling) ──────────────────
//
// The flagship flow. `startTailor` kicks off analysis and returns a run_id in
// <1s; the caller polls `fetchTailorRun` through the lifecycle
// (analyzing → pending_questions → generating → done | error). Answers are
// posted with `submitTailorAnswers`. Downloads reuse `downloadResume` above —
// it already sends the (possibly user-edited) resume JSON in the body.

export type TailorRunStatus =
  | "analyzing"
  | "pending_questions"
  | "generating"
  | "done"
  | "error";

export type TailorRunState = {
  run_id: string;
  status: TailorRunStatus;
  demo_mode: boolean;
  /** True when this result was served from the 7-day cache (no model call). */
  cached: boolean;
  /** The gap analysis (questions etc.) — present from `pending_questions` on. */
  analysis: Analysis | null;
  /** The tailored resume — PARTIAL while `generating`, final on `done`.
   * Always null in "docx_inject" mode (the result is the saved DOCX, edited
   * in place — downloaded, not edited as JSON). */
  resume: TailoredResume | null;
  error: string | null;
  /** "generate" (editable resume JSON) or "docx_inject" (in-place keyword edits
   * on the user's saved DOCX → download). Drives which result UI shows. */
  mode?: "generate" | "docx_inject";
  /** docx_inject only: counts of keyword edits applied / skipped. */
  docx_applied?: number | null;
  docx_skipped?: number | null;
};

/** Poll cadence + ceiling for a tailor run. Generation is hard-capped at 90s
 * server-side; 120s of polling gives the worker headroom to write its
 * terminal row before the client gives up. Cadence tightened 1500ms → 700ms
 * (PR: tailor latency) so streamed sections surface ~2x sooner — the poll
 * interval is the floor on how fast the run-based UI can feel. These are
 * cheap GETs, so the extra request volume is negligible. */
export const TAILOR_POLL_INTERVAL_MS = 700;
export const TAILOR_MAX_WAIT_MS = 120_000;

/** Thrown by `startTailor` when the profile is too empty to tailor from
 * (HTTP 409, code="profile_thin"). The UI offers "Go to Profile" vs
 * "Generate anyway" (which re-calls with force=true). */
export class ProfileThinError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ProfileThinError";
  }
}

/** Kick off a tailor run. Returns the run_id to poll. Throws
 * `ProfileThinError` on the thin-profile gate unless `force` is set. */
export async function startTailor(
  jobId: number,
  force = false,
): Promise<{ run_id: string }> {
  const res = await fetch(`${API_URL}/api/tailor/start`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_id: jobId, force }),
  });
  if (res.status === 409) {
    let message =
      "Your profile is mostly empty — tailoring works best once you've filled in your experience.";
    try {
      const body = await res.json();
      if (body?.detail?.message) message = body.detail.message as string;
    } catch {
      // keep the default message
    }
    throw new ProfileThinError(message);
  }
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as { run_id: string };
}

/** Fetch the current state of a tailor run. Cheap; safe to poll. */
export async function fetchTailorRun(runId: string): Promise<TailorRunState> {
  const res = await fetch(
    `${API_URL}/api/tailor/runs/${encodeURIComponent(runId)}`,
    { credentials: "include" },
  );
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as TailorRunState;
}

/** Submit answers to the gap questions and kick off generation. Re-postable
 * to retry a run that landed in `error`. */
export async function submitTailorAnswers(
  runId: string,
  answers: Record<string, string>,
): Promise<void> {
  const res = await fetch(
    `${API_URL}/api/tailor/runs/${encodeURIComponent(runId)}/answers`,
    {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    },
  );
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
}


// ── Auth (Google sign-in) ────────────────────────────────────────────────

export type CurrentUser = {
  id: number;
  email: string;
  name: string | null;
  /** True once the user has explicitly saved their profile (any
   * `PUT /api/profile`). False on a brand-new account with the
   * auto-seeded demo profile. The frontend uses this to gate
   * `/jobs` — newcomers go to `/profile` first so the tailoring
   * flow never runs against the demo template. Optional on the
   * type for backwards compatibility with older backends that
   * don't emit the field. */
  profile_saved?: boolean;
  /** True iff the user's email is in the backend `ADMIN_EMAILS`
   * allowlist. Used to hide admin UI (manual-entry controls, the
   * /admin nav link). The BACKEND `require_admin_user` dependency
   * is the actual access gate — hiding the UI is cosmetic, not
   * an authorisation control. */
  is_admin?: boolean;
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

// ── Browser extension (Connected devices + Saved answers) ───────────────────

export type ExtensionSession = {
  id: string;
  device_name: string | null;
  created_at: string;
  last_used_at: string | null;
  revoked: boolean;
};

export type SavedQA = {
  id: string;
  question_canonical: string;
  answer: string;
  field_type: string;
  times_used: number;
  source_ats: string | null;
  updated_at: string;
};

/** Mint an extension bearer token (cookie-authed). Called by the
 * /extension/connect page. */
export async function createExtensionSession(deviceName?: string): Promise<{
  token: string;
  session_id: string;
}> {
  const res = await fetch(`${API_URL}/api/extension/sessions/create`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_name: deviceName ?? null }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return res.json();
}

export async function listExtensionSessions(): Promise<ExtensionSession[]> {
  const res = await fetch(`${API_URL}/api/extension/sessions`, { credentials: "include" });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return res.json();
}

export async function revokeExtensionSession(sessionId: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/extension/sessions/revoke`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
}

export async function listSavedQA(): Promise<SavedQA[]> {
  const res = await fetch(`${API_URL}/api/extension/qa/list`, { credentials: "include" });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return res.json();
}

export async function updateSavedQA(
  id: string,
  patch: { answer?: string; question_canonical?: string },
): Promise<SavedQA> {
  const res = await fetch(`${API_URL}/api/extension/qa/${encodeURIComponent(id)}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return res.json();
}

export async function deleteSavedQA(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/extension/qa/${encodeURIComponent(id)}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
}

// ── Active resume (one saved resume per user) ───────────────────────────────

export type ActiveResume =
  | { present: false }
  | { present: true; filename: string; content_type: string; uploaded_at: string | null };

export async function getActiveResume(): Promise<ActiveResume> {
  const res = await fetch(`${API_URL}/api/profile/active-resume`, { credentials: "include" });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return res.json();
}

export async function uploadActiveResume(file: File): Promise<ActiveResume> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/profile/active-resume`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return res.json();
}

export async function deleteActiveResume(): Promise<void> {
  const res = await fetch(`${API_URL}/api/profile/active-resume`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok && res.status !== 204) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
}

export function activeResumeDownloadUrl(): string {
  return `${API_URL}/api/profile/active-resume/download`;
}
