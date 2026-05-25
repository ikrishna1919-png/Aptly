import { Badge } from "@/components/ui/badge";
import { JobCard } from "@/components/job-card";
import { JobFilters } from "@/components/job-filters";
import { fetchHealth, fetchJobs, type JobsQuery } from "@/lib/api";

export const dynamic = "force-dynamic";

type SearchParams = Record<string, string | string[] | undefined>;

function first(v: string | string[] | undefined): string | undefined {
  if (Array.isArray(v)) return v[0];
  return v;
}

function buildQuery(sp: SearchParams): JobsQuery {
  const q: JobsQuery = {};
  const text = first(sp.q);
  if (text) q.q = text;
  const company = first(sp.company);
  if (company) q.company = company;
  const location = first(sp.location);
  if (location) q.location = location;
  const remote = first(sp.remote);
  if (remote === "true") q.remote = true;
  if (remote === "false") q.remote = false;
  const sponsors = first(sp.sponsors_visa);
  if (sponsors === "true") q.sponsors_visa = true;
  if (sponsors === "false") q.sponsors_visa = false;
  const et = first(sp.employment_type);
  if (et) q.employment_type = et;
  return q;
}

export default async function Page({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const query = buildQuery(searchParams);
  const [health, jobsResult] = await Promise.all([
    fetchHealth(),
    fetchJobs(query).catch((e: unknown) => ({
      error: e instanceof Error ? e.message : "Unknown error",
    })),
  ]);

  const errored = "error" in jobsResult;
  const data = errored ? null : jobsResult;

  return (
    <main className="container mx-auto max-w-4xl space-y-8 py-10">
      <header className="space-y-3">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">Phase 1 — Real jobs</Badge>
          {health ? (
            <Badge variant="outline" className="text-xs">
              API: {health.status} · DB: {health.database}
            </Badge>
          ) : (
            <Badge variant="destructive" className="text-xs">
              API offline
            </Badge>
          )}
        </div>
        <h1 className="text-4xl font-bold tracking-tight">Aptly</h1>
        <p className="text-muted-foreground">
          Real, fresh job postings from public ATS boards. The feed is a
          strict {data?.window_hours ?? 48}-hour rolling window — anything
          older is removed.
        </p>
      </header>

      <JobFilters />

      {errored && (
        <p className="text-sm text-destructive">
          Could not load jobs: {(jobsResult as { error: string }).error}
        </p>
      )}

      {data && (
        <>
          <p className="text-sm text-muted-foreground">
            {data.total === 0
              ? "No jobs match these filters in the current window."
              : `${data.total} job${data.total === 1 ? "" : "s"} in the last ${data.window_hours} hours.`}
          </p>
          <div className="space-y-4">
            {data.jobs.map((job) => (
              <JobCard key={job.id} job={job} />
            ))}
          </div>
        </>
      )}
    </main>
  );
}
