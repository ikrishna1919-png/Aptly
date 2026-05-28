import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { CompanyMark } from "@/components/company-mark";
import { JobDescription } from "@/components/job-description";
import { TailorPanel } from "@/components/tailor-panel";
import { fetchJob, MANUAL_SOURCE, type Job } from "@/lib/api";
import { formatLongDate, formatRelative } from "@/lib/utils";

/**
 * Strip HTML tags for the `<meta name="description">` value. Runs in
 * the server function so the JD sanitizer can stay client-only (the
 * whole reason this file no longer imports DOMPurify at the top
 * level). A regex strip is sufficient here because the result is
 * never injected back into the DOM — it goes into a meta tag value
 * which is plain-text only.
 */
function stripTagsForMeta(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: { id: string };
}): Promise<Metadata> {
  const id = Number(params.id);
  if (!Number.isFinite(id)) return { title: "Job not found" };
  const job = await fetchJob(id).catch(() => null);
  if (!job) return { title: "Job not found" };
  const plainDescription = job.description
    ? stripTagsForMeta(job.description).slice(0, 200) || undefined
    : undefined;
  return {
    title: `${job.title} at ${job.company}`,
    description: plainDescription,
  };
}

export default async function JobDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const id = Number(params.id);
  if (!Number.isFinite(id) || id < 1) notFound();

  const job = await fetchJob(id);
  if (!job) notFound();

  const hasH1bSignal = job.sponsors_h1b || job.past_h1b_activity;

  return (
    <article className="container max-w-3xl py-8 sm:py-12">
      {/* Breadcrumb back to the feed. Small but real — the previous
          single-link version pretended to be a back arrow but had no
          breadcrumb context. */}
      <nav aria-label="Breadcrumb" className="mb-6 text-sm">
        <Link
          href="/jobs"
          className="inline-flex items-center gap-1.5 rounded-md px-1.5 py-0.5 text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <BackArrow />
          <span>All jobs</span>
        </Link>
      </nav>

      {/*
       * Top block: hierarchy from largest to smallest is
       *   1. Title (largest, the page's reason for existing).
       *   2. Company (one rung down — informative, not the headline).
       *   3. Sponsorship badges (the differentiator — surfaced
       *      directly under the company so a sponsor-seeking user
       *      can confirm at a glance).
       *   4. Location · type · remote (smallest meta line).
       *   5. CTA — the visual anchor below the badges.
       * Everything outside the header sits below a separator so the
       * eye knows the introduction has ended.
       */}
      <header className="space-y-5">
        <div className="flex items-start gap-4">
          <CompanyMark name={job.company} size="lg" />
          <div className="min-w-0 flex-1 space-y-2">
            <h1 className="text-balance text-3xl font-semibold leading-[1.1] tracking-tight text-foreground sm:text-4xl">
              {job.title}
            </h1>
            <p className="text-base font-medium text-foreground">
              {job.company}
            </p>
          </div>
        </div>

        {/* Sponsorship row — promoted high in the hierarchy with a
            "What does this mean?" affordance immediately adjacent.
            A bare <details> gives us a no-JS expand with semantic
            keyboard support; styling lifts it into the rest of the
            type scale. */}
        {hasH1bSignal && (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-1.5">
              {job.sponsors_h1b && (
                <Badge
                  variant="solid"
                  className="px-2.5 py-1 text-xs font-semibold shadow-sm"
                  title={`Filed ${job.lca_count_12mo} H-1B LCAs in the past 12 months (public DOL data).`}
                >
                  <SparkleIcon />
                  Sponsors H-1B
                </Badge>
              )}
              {!job.sponsors_h1b && job.past_h1b_activity && (
                <Badge
                  variant="default"
                  className="px-2.5 py-1 text-xs"
                  title={`Filed ${job.lca_count_3yr} H-1B LCAs in the past 3 years (public DOL data).`}
                >
                  Past H-1B activity
                </Badge>
              )}
              {job.sponsors_visa === true && (
                <Badge variant="highlight">Sponsors visa</Badge>
              )}
            </div>

            <details className="group rounded-md">
              <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-md text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                <InfoIcon />
                <span>What do these mean?</span>
                <span className="text-muted-foreground/60 transition-transform group-open:rotate-180">
                  <CaretIcon />
                </span>
              </summary>
              <div className="mt-2 max-w-prose space-y-1.5 rounded-md border border-border/60 bg-muted/30 p-3 text-xs leading-relaxed text-muted-foreground">
                <p>
                  <span className="font-medium text-foreground">Sponsors H-1B</span>{" "}
                  — this employer filed at least five H-1B LCAs in the past 12
                  months ({job.lca_count_12mo} on file). High-confidence signal
                  that they have an active sponsorship pipeline.
                </p>
                <p>
                  <span className="font-medium text-foreground">
                    Past H-1B activity
                  </span>{" "}
                  — at least one LCA in the past 3 years ({job.lca_count_3yr} on
                  file). Lower-confidence — activity may be stale.
                </p>
                <p className="pt-1">
                  Signals come from public DOL LCA disclosure data. The data
                  is incomplete, employer-name mismatches happen, and a signal
                  does not guarantee sponsorship for any specific role.
                </p>
              </div>
            </details>
          </div>
        )}

        {/* Lower-priority meta row — location, type, remote, salary —
            wrapped in subdued type so it reads as supporting info. */}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm text-muted-foreground">
          {job.location && (
            <span className="inline-flex items-center gap-1">
              <LocationIcon /> {job.location}
            </span>
          )}
          {job.employment_type && (
            <span className="inline-flex items-center gap-1">
              <Dot /> {job.employment_type}
            </span>
          )}
          {job.remote === true && (
            <span className="inline-flex items-center gap-1">
              <Dot /> Remote
            </span>
          )}
          {job.remote === false && (
            <span className="inline-flex items-center gap-1">
              <Dot /> On-site
            </span>
          )}
          {job.salary && (
            <span className="inline-flex items-center gap-1">
              <Dot />
              <span className="font-medium text-foreground">{job.salary}</span>
            </span>
          )}
        </div>

        {/* CTA row. "Apply on …" is the visual anchor — solid primary,
            full-width on phone, auto-width with a meta line on
            desktop. Posted-date / freshness slots in as quiet meta
            next to it. */}
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          <Button
            asChild
            size="lg"
            className="w-full rounded-full text-base font-semibold shadow-sm sm:w-auto"
          >
            <a
              href={job.url}
              target="_blank"
              rel="noopener noreferrer"
              aria-label={`Apply to ${job.title} at ${job.company}`}
            >
              Apply on {prettySource(job.source)}
              <ExternalIcon />
            </a>
          </Button>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <Meta label="Posted" value={formatLongDate(job.posted_at)} />
            <Meta label="Updated" value={formatRelative(job.source_updated_at)} />
            {job.source === MANUAL_SOURCE && (
              <Badge variant="muted">Curated</Badge>
            )}
          </div>
        </div>
      </header>

      <Separator className="my-8" />

      <section className="mb-10" aria-labelledby="tailor-heading">
        <h2 id="tailor-heading" className="sr-only">
          Tailor my resume
        </h2>
        <TailorPanel job={job} />
      </section>

      {job.skills.length > 0 && (
        <section className="mb-10" aria-labelledby="skills-heading">
          <h2
            id="skills-heading"
            className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
          >
            Skills detected
          </h2>
          <div className="flex flex-wrap gap-1.5">
            {job.skills.map((s) => (
              <span
                key={s}
                className="rounded-md bg-secondary px-2 py-1 text-xs font-medium text-secondary-foreground"
              >
                {s}
              </span>
            ))}
          </div>
        </section>
      )}

      <section aria-labelledby="jd-heading">
        <h2
          id="jd-heading"
          className="mb-4 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Description
        </h2>
        {/* The prose container caps reading width — `max-w-prose`
            keeps lines at a comfortable ~65ch even on a wide
            viewport. `JobDescription` is null-safe and applies its
            own typography classes inside. */}
        <div className="max-w-prose">
          <JobDescription html={job.description} />
        </div>
      </section>

      <Separator className="my-10" />

      <footer className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>
          Source:{" "}
          <span className="font-medium text-foreground">
            {prettySource(job.source)}
          </span>{" "}
          · ID {job.external_id}
        </span>
        <Button asChild variant="outline" size="sm" className="rounded-full">
          <a href={job.url} target="_blank" rel="noopener noreferrer">
            View original
            <ExternalIcon />
          </a>
        </Button>
      </footer>
    </article>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <span className="inline-flex items-center gap-1">
      <span className="font-medium text-foreground/80">{label}:</span>
      <span>{value}</span>
    </span>
  );
}

function prettySource(source: string): string {
  if (source === MANUAL_SOURCE) return "Aptly";
  return source.charAt(0).toUpperCase() + source.slice(1);
}

function BackArrow() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M19 12H5m7-7-7 7 7 7" />
    </svg>
  );
}

function LocationIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="13"
      height="13"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="opacity-70"
    >
      <path d="M12 21s-7-7.5-7-12a7 7 0 1 1 14 0c0 4.5-7 12-7 12Z" />
      <circle cx="12" cy="9" r="2.5" />
    </svg>
  );
}

function Dot() {
  return <span aria-hidden="true">·</span>;
}

function ExternalIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="ml-1.5"
    >
      <path d="M15 3h6v6M10 14 21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
    </svg>
  );
}

function InfoIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="12"
      height="12"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="9" />
      <path d="M12 8v.01M11 12h1v5h1" />
    </svg>
  );
}

function CaretIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="10"
      height="10"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

function SparkleIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="11"
      height="11"
      fill="currentColor"
      aria-hidden="true"
    >
      <path d="M12 2 13.6 8.4 20 10l-6.4 1.6L12 18l-1.6-6.4L4 10l6.4-1.6Z" />
    </svg>
  );
}
