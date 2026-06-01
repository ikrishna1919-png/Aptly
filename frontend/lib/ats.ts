// API client for the /ats resume hub. Runs are backed by the tailor_runs
// table and driven by background workers; the client polls /api/ats/runs/{id}.

import { type TailoredResume } from "./api";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "";

export type AtsOption = "jd" | "upload";
export type AtsFormat = "modern" | "classic" | "minimal" | "plain" | "custom";

export type AtsCustomOptions = {
  base: AtsFormat;
  accent_color: string;
  font_family: "sans" | "serif";
  margins: "tight" | "normal" | "loose";
};

export type AtsQuestions = {
  length: "1" | "2";
  tone: "formal" | "confident" | "conversational";
  emphasis: "technical" | "leadership" | "execution" | "mixed";
  skills: string[];
  roles: string[];
  additional: string;
  // Option-B (match-my-format) gap/relevance fields. Optional so the option-A
  // generate flow is unaffected; they steer WHICH existing wording to rewrite.
  missing_experience?: string;
  metrics?: string;
  // Yes/No gap-confirmation answers (option B): each JD gap skill the candidate
  // confirms (or not), with optional details. Only "yes" entries steer rewrites.
  gaps?: { question: string; answer: "yes" | "no"; details: string }[];
};

export type DocxEdit = { original_text: string; replacement_text: string; reason?: string };

export type AtsRunStatus = "analyzing" | "generating" | "done" | "error";

export type AtsRun = {
  run_id: string;
  status: AtsRunStatus;
  option_type: "jd_paste" | "upload_docx" | "upload_pdf_fallback" | null;
  demo_mode: boolean;
  format: string | null;
  resume: TailoredResume | null;
  diff: { applied: DocxEdit[]; skipped: DocxEdit[] } | null;
  // Post-generation JD keyword coverage of the tailored resume (generate paths).
  coverage: { percent: number; matched: string[]; missing: string[] } | null;
  error: string | null;
};

export const ATS_POLL_MS = 800;
export const ATS_MAX_WAIT_MS = 120_000;

async function detail(res: Response): Promise<string> {
  try {
    const b = await res.json();
    if (typeof b?.detail === "string") return b.detail;
  } catch {
    /* ignore */
  }
  return `Failed (${res.status})`;
}

/** Upload a DOCX/PDF. Returns {kind:"docx", upload_id} or {kind:"pdf"}. */
export async function parseAtsUpload(
  file: File,
): Promise<{ kind: "docx"; upload_id: string } | { kind: "pdf" }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/ats/parse-upload`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export type GeneratePayload = {
  option_type: "jd_paste" | "upload_docx" | "upload_pdf_fallback";
  jd_text: string;
  questions: AtsQuestions;
  format: AtsFormat;
  custom_options?: AtsCustomOptions | null;
  upload_id?: string | null;
};

export async function startAtsGenerate(payload: GeneratePayload): Promise<{ run_id: string }> {
  const res = await fetch(`${API_URL}/api/ats/generate`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export async function fetchAtsRun(runId: string): Promise<AtsRun> {
  const res = await fetch(`${API_URL}/api/ats/runs/${encodeURIComponent(runId)}`, {
    credentials: "include",
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

/** Mark a completed tailor run as the user's active autofill resume — the one
 * the Chrome extension fills with by default. Sets a pointer only; it does NOT
 * push a file into the browser. */
export async function setActiveAutofillRun(runId: string): Promise<void> {
  const res = await fetch(`${API_URL}/api/ats/active-autofill-run`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ run_id: runId }),
  });
  if (!res.ok) throw new Error(await detail(res));
}

/** Download the keyword-injected DOCX (Option 2). `accepted` = indices into
 * the run's applied edits to keep (omit = all). `filename` = optional output
 * name (sanitized server-side; omit = default). */
export async function downloadAtsDocx(
  runId: string,
  accepted?: number[],
  filename?: string,
): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/ats/runs/${encodeURIComponent(runId)}/download-docx`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accepted: accepted ?? null, filename: filename ?? null }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.blob();
}

// ── Hub additions: coverage, default formats, cover letters, LinkedIn ───────

export type Coverage = { percent: number; matched: string[]; missing: string[] };

export async function keywordCoverage(jdText: string): Promise<Coverage> {
  const res = await fetch(`${API_URL}/api/ats/keyword-coverage`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jd_text: jdText }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

// `source` (resume kind only): "ai" | "resume" | "available" — the tailoring
// SOURCE that routes generate vs in-place docx-inject. null/absent → "ai".
export type ResumeSource = "ai" | "resume" | "available";
export type DefaultFormat = {
  kind: string;
  format: string;
  custom: unknown;
  reason?: string;
  source?: ResumeSource | null;
};

export async function getDefaultFormat(kind: "resume" | "cover"): Promise<DefaultFormat> {
  const res = await fetch(`${API_URL}/api/ats/default-format/${kind}`, { credentials: "include" });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export async function setDefaultFormat(
  kind: "resume" | "cover",
  format: string,
  custom?: unknown,
  source?: ResumeSource,
): Promise<DefaultFormat> {
  const res = await fetch(`${API_URL}/api/ats/default-format`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ kind, format, custom: custom ?? null, source: source ?? null }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export async function aiChooseFormat(kind: "resume" | "cover"): Promise<DefaultFormat> {
  const res = await fetch(`${API_URL}/api/ats/default-format/ai-choose/${kind}`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export type CoverLetter = {
  id: string;
  status: string;
  demo_mode: boolean;
  format: string | null;
  content: {
    date: string;
    recipient: string;
    greeting: string;
    paragraphs: string[];
    closing: string;
    signature: string;
  } | null;
  error: string | null;
};

export async function generateCoverLetter(payload: {
  jd_text: string;
  company_name?: string;
  hook?: string;
  questions: { tone: string; length: string; opening: string; additional: string };
}): Promise<CoverLetter> {
  const res = await fetch(`${API_URL}/api/cover-letter/generate`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export async function updateCoverLetter(id: string, content: unknown): Promise<CoverLetter> {
  const res = await fetch(`${API_URL}/api/cover-letter/${encodeURIComponent(id)}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}

export function coverLetterDownloadUrl(id: string, fmt: "docx" | "pdf"): string {
  return `${API_URL}/api/cover-letter/${encodeURIComponent(id)}/download?fmt=${fmt}`;
}

export async function linkedinImport(file: File): Promise<{ imported: Record<string, unknown>; diff: unknown }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/api/ats/linkedin-import`, {
    method: "POST",
    credentials: "include",
    body: form,
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.json();
}
