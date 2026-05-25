import { Suspense } from "react";

import { JobFeedSkeleton } from "@/components/job-feed-skeleton";
import { JobFilters } from "@/components/job-filters";
import { JobsFeed } from "@/components/job-feed";
import type { JobsQuery } from "@/lib/api";

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

// Keys for the Suspense reset — when query params change, we want a fresh
// fallback (skeleton) while the new feed loads.
function suspenseKey(q: JobsQuery) {
  return JSON.stringify(q);
}

export default function Page({ searchParams }: { searchParams: SearchParams }) {
  const query = buildQuery(searchParams);

  return (
    <>
      <section className="hero-glow relative">
        <div className="container py-12 sm:py-16">
          <div className="max-w-2xl space-y-4">
            <p className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-card/70 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              <span className="h-1.5 w-1.5 rounded-full bg-primary" />
              Live · 48h rolling window
            </p>
            <h1 className="text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl">
              Jobs that{" "}
              <span className="bg-gradient-to-br from-primary to-primary/70 bg-clip-text text-transparent">
                move.
              </span>
            </h1>
            <p className="max-w-xl text-base text-muted-foreground sm:text-lg">
              Fresh postings from real ATS boards (Greenhouse, Lever), filtered
              for what matters: visa sponsorship, location, skills. Updated every
              six hours — stale roles age out automatically.
            </p>
          </div>
        </div>
      </section>

      <section className="container space-y-6 pb-16">
        <JobFilters />
        <Suspense key={suspenseKey(query)} fallback={<JobFeedSkeleton />}>
          <JobsFeed query={query} />
        </Suspense>
      </section>
    </>
  );
}
