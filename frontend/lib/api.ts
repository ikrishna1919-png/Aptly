export type Job = {
  id: number;
  source: string;
  external_id: string;
  company: string;
  title: string;
  location: string | null;
  remote: boolean | null;
  employment_type: string | null;
  skills: string[];
  sponsors_visa: boolean | null;
  url: string;
  description: string | null;
  posted_at: string | null;
  source_updated_at: string | null;
};

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
