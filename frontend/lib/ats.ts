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

/** Download the keyword-injected DOCX (Option 2). `accepted` = indices into
 * the run's applied edits to keep (omit = all). */
export async function downloadAtsDocx(runId: string, accepted?: number[]): Promise<Blob> {
  const res = await fetch(`${API_URL}/api/ats/runs/${encodeURIComponent(runId)}/download-docx`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ accepted: accepted ?? null }),
  });
  if (!res.ok) throw new Error(await detail(res));
  return res.blob();
}
