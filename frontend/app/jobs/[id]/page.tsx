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
  // The stored description is HTML; strip every tag for the meta
  // description so search engines / link previews see clean text
  // rather than a half-truncated `<p>` fragment.
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

  return (
    <article className="container max-w-3xl py-10">
      <Link
        href="/"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <BackArrow /> All jobs
      </Link>

      <header className="mt-6 space-y-4">
        <div className="flex items-start gap-4">
          <CompanyMark name={job.company} size="lg" />
          <div className="min-w-0 flex-1 space-y-1">
            <p className="text-sm font-medium uppercase tracking-wider text-muted-foreground">
              {job.company}
            </p>
            <h1 className="text-balance text-3xl font-semibold leading-tight tracking-tight sm:text-4xl">
              {job.title}
            </h1>
            <p className="text-sm text-muted-foreground">
              {[
                job.location,
                job.employment_type,
                job.remote === true ? "Remote" : job.remote === false ? "On-site" : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
        </div>

        <BadgeRow job={job} />

        <div className="flex flex-wrap items-center gap-3">
          <Button asChild className="rounded-full">
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
          <Meta label="Posted" value={formatLongDate(job.posted_at)} />
          <Meta
            label="Last updated"
            value={formatRelative(job.source_updated_at)}
          />
        </div>
      </header>

      <Separator className="my-8" />

      <section className="mb-8" aria-labelledby="tailor-heading">
        <h2 id="tailor-heading" className="sr-only">
          Tailor my resume
        </h2>
        <TailorPanel job={job} />
      </section>

      {job.skills.length > 0 && (
        <section className="mb-8" aria-labelledby="skills-heading">
          <h2 id="skills-heading" className="mb-2 text-sm font-semibold">
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
        <h2 id="jd-heading" className="mb-3 text-sm font-semibold">
          Description
        </h2>
        {/* `JobDescription` is null-safe — it renders the fallback if
            `html` is null, empty, or fully sanitised away. */}
        <JobDescription html={job.description} />
      </section>

      <Separator className="my-8" />

      <footer className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
        <span>
          Source: <span className="font-medium text-foreground">{prettySource(job.source)}</span>{" "}
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

function BadgeRow({ job }: { job: Job }) {
  return (
    <div>
      <div className="flex flex-wrap gap-1.5">
        {job.remote === true && <Badge variant="solid">Remote</Badge>}
        {job.remote === false && <Badge variant="outline">On-site</Badge>}
        {job.employment_type && <Badge variant="outline">{job.employment_type}</Badge>}
        {job.salary && <Badge variant="highlight">{job.salary}</Badge>}
        {job.sponsors_visa === true && (
          <Badge variant="highlight">Sponsors visa</Badge>
        )}
        {job.sponsors_visa === false && (
          <Badge variant="outline">No visa sponsorship</Badge>
        )}
        {/* H-1B LCA signals — two distinct badges. See JobCard for
            the same rendering rules; a company with no DOL history
            gets NEITHER badge (never a "doesn't sponsor" badge). */}
        {job.sponsors_h1b && (
          <Badge
            variant="highlight"
            title={`Filed ${job.lca_count_12mo} H-1B LCAs in the past 12 months (public DOL data).`}
          >
            Sponsors H-1B
          </Badge>
        )}
        {!job.sponsors_h1b && job.past_h1b_activity && (
          <Badge
            variant="outline"
            title={`Filed ${job.lca_count_3yr} H-1B LCAs in the past 3 years (public DOL data).`}
          >
            Past H-1B activity
          </Badge>
        )}
        {job.source === MANUAL_SOURCE && <Badge variant="muted">Curated</Badge>}
      </div>
      {(job.sponsors_h1b || job.past_h1b_activity) && (
        <p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">
          H-1B signals reflect public DOL LCA filings. The data is incomplete,
          employer-name mismatches happen, and a signal does not guarantee
          sponsorship for any specific role.
        </p>
      )}
    </div>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <span className="text-xs text-muted-foreground">
      <span className="font-medium text-foreground/80">{label}:</span> {value}
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
