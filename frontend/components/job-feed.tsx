import Link from "next/link";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { JobCard } from "@/components/job-card";
import { fetchJobs, type JobsQuery } from "@/lib/api";

export async function JobsFeed({ query }: { query: JobsQuery }) {
  let data;
  try {
    data = await fetchJobs(query);
  } catch (e) {
    return (
      <ErrorState
        title="Couldn't load jobs"
        description={
          <>
            The API didn&apos;t respond. Check that the backend is up and{" "}
            <code className="rounded bg-muted px-1">NEXT_PUBLIC_API_URL</code> is set.
            {e instanceof Error ? ` (${e.message})` : null}
          </>
        }
      />
    );
  }

  if (data.total === 0) {
    return (
      <EmptyState
        title="No jobs match these filters"
        description={
          <>
            Try clearing some filters or check back after the next 6-hour ingest.
            The feed only ever shows the last {data.window_hours} hours.
          </>
        }
        action={
          <Link
            href="/"
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            Clear filters
          </Link>
        }
      />
    );
  }

  return (
    <>
      <div
        className="flex items-baseline justify-between"
        aria-live="polite"
      >
        <p className="text-sm text-muted-foreground">
          <span className="font-medium text-foreground">{data.total}</span>{" "}
          {data.total === 1 ? "job" : "jobs"} · last {data.window_hours}h
        </p>
        <p className="text-xs text-muted-foreground">Sorted by newest</p>
      </div>
      <ul className="grid gap-3">
        {data.jobs.map((job) => (
          <li key={job.id} className="animate-fade-in">
            <JobCard job={job} />
          </li>
        ))}
      </ul>
    </>
  );
}
