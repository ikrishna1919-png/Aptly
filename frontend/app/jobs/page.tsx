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
  // H-1B filters only honour `true`. The backend rejects `false` as a
  // negative filter (silence in the DOL data isn't evidence) so we
  // never forward it.
  const sponsorsH1b = first(sp.sponsors_h1b);
  if (sponsorsH1b === "true") q.sponsors_h1b = true;
  const pastH1b = first(sp.past_h1b_activity);
  if (pastH1b === "true") q.past_h1b_activity = true;
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
        <div className="container py-10 sm:py-14">
          <div className="max-w-2xl space-y-4">
            <p className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-card/70 px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
              <span className="h-1.5 w-1.5 rounded-full bg-primary" />
              Live · 48h rolling window
            </p>
            <h1 className="text-balance text-4xl font-semibold leading-[1.05] tracking-tight sm:text-5xl">
              Jobs that{" "}
              <span className="bg-gradient-to-br from-primary to-primary/70 bg-clip-text text-transparent">
                sponsor.
              </span>
            </h1>
            <p className="max-w-xl text-base text-muted-foreground sm:text-lg">
              Fresh ATS-sourced postings, surfaced with H-1B sponsorship
              signals from public DOL filings — so you can spot the
              companies actually hiring international talent before you
              spend an evening tailoring a resume.
            </p>
          </div>
        </div>
      </section>

      <section className="container space-y-5 pb-16 sm:space-y-6">
        <JobFilters />
        <Suspense key={suspenseKey(query)} fallback={<JobFeedSkeleton />}>
          <JobsFeed query={query} />
        </Suspense>
      </section>
    </>
  );
}
