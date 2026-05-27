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
  });
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`);
  }
  return (await res.json()) as JobsResponse;
}

export async function fetchJob(id: number): Promise<Job | null> {
  const res = await fetch(`${API_URL}/api/jobs/${id}`, { cache: "no-store" });
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
    const res = await fetch(`${API_URL}/api/health`, { cache: "no-store" });
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

export async function fetchProfile(token: string): Promise<Profile> {
  const res = await fetch(`${API_URL}/api/admin/profile`, {
    headers: { "X-Admin-Token": token },
    cache: "no-store",
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as Profile;
}

export async function saveProfile(profile: Profile, token: string): Promise<Profile> {
  const res = await fetch(`${API_URL}/api/admin/profile`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-Admin-Token": token },
    body: JSON.stringify(profile),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return (await res.json()) as Profile;
}

// Parse runs as a background job on the backend:
//   POST /api/admin/profile/parse  → 202 { run_id, status_url }
//   GET  /api/admin/profile/parse/{run_id}  → { status, profile?, error? }
//
// The kick-off POST returns immediately (the long Anthropic call no
// longer blocks the HTTP request, which is why this fix exists). The
// frontend polls the status URL until the row settles at
// `success` (with `profile`) or `failed` (with `error`).

export const PARSE_POLL_INTERVAL_MS = 2_000;
// Anthropic's own deadline is 90s; we wait a touch longer on the
// client so a row that genuinely finished doesn't get cut off by the
// polling cap. If `parseProfileText` hits this without seeing a
// terminal state, it throws `ParseTimeoutError`.
export const PARSE_MAX_WAIT_MS = 120_000;

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
      "Parse took too long. The resume may be too long, or the AI service is slow right now — try a shorter resume and retry.",
    );
    this.name = "ParseTimeoutError";
  }
}

/** Kick off the parse. Returns the run_id the caller can poll. */
export async function startParse(
  text: string,
  token: string,
): Promise<{ run_id: string }> {
  const res = await fetch(`${API_URL}/api/admin/profile/parse`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Admin-Token": token },
    body: JSON.stringify({ text }),
  });
  if (!res.ok) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
  const body = (await res.json()) as { run_id: string };
  return body;
}

/** Fetch the current state of a parse run. */
export async function fetchParseRun(run_id: string, token: string): Promise<ParseRun> {
  const res = await fetch(
    `${API_URL}/api/admin/profile/parse/${encodeURIComponent(run_id)}`,
    { headers: { "X-Admin-Token": token } },
  );
  if (!res.ok) {
    throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  }
  return (await res.json()) as ParseRun;
}

/**
 * Kick off a parse and poll until it lands at `success` or `failed`.
 * Returns the parsed Profile on success; throws on failure or timeout.
 * `onProgress` (if supplied) fires once with `"running"` after the
 * kick-off so callers can flip a UI spinner; it doesn't fire again
 * because nothing else changes until the terminal state.
 */
export async function parseProfileText(
  text: string,
  token: string,
  opts: { signal?: AbortSignal; onProgress?: (status: ParseRunStatus) => void } = {},
): Promise<Profile> {
  const { signal, onProgress } = opts;
  const { run_id } = await startParse(text, token);
  onProgress?.("running");

  const deadline = Date.now() + PARSE_MAX_WAIT_MS;
  while (Date.now() < deadline) {
    if (signal?.aborted) {
      throw new DOMException("Parse polling aborted", "AbortError");
    }
    const run = await fetchParseRun(run_id, token);
    if (run.status === "success") {
      if (!run.profile) {
        // Defence-in-depth: a `success` row without a profile means
        // the backend bug-out path elided the payload. Don't crash —
        // surface a generic message.
        throw new Error("Parse succeeded but no profile was returned. Retry.");
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
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resume, filename }),
  });
  if (!res.ok) throw new Error((await safeDetail(res)) || `Failed (${res.status})`);
  return await res.blob();
}
