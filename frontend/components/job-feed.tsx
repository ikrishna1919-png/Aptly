import Link from "next/link";

import { EmptyState, ErrorState } from "@/components/empty-state";
import { JobCard } from "@/components/job-card";
import { JobsPagination } from "@/components/jobs-pagination";
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
        title="No jobs match your filters yet"
        description={
          <>
            Try widening the location, removing a filter, or check back later.
          </>
        }
        action={
          <Link
            href="/jobs"
            className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          >
            Clear filters
          </Link>
        }
      />
    );
  }

  // Result-count line keeps the technical 48h-window detail OUT of
  // the user-facing surface (per the cleanup spec). Just the count
  // + sort affordance.
  const first = data.offset + 1;
  const last = Math.min(data.offset + data.jobs.length, data.total);
  return (
    <>
      <div
        className="flex items-baseline justify-between"
        aria-live="polite"
      >
        <p className="text-sm text-muted-foreground">
          Showing{" "}
          <span className="font-medium text-foreground">
            {first}–{last}
          </span>{" "}
          of{" "}
          <span className="font-medium text-foreground">{data.total}</span>{" "}
          {data.total === 1 ? "job" : "jobs"}
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
      <JobsPagination
        page={data.page}
        totalPages={data.total_pages}
        limit={data.limit}
      />
    </>
  );
}
