import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { Separator } from "@/components/ui/separator";
import { ApplyButton } from "@/components/jobs/apply-button";
import { BackToList } from "@/components/jobs/back-to-list";
import { SponsorshipInsights } from "@/components/jobs/sponsorship";
import { TailorCta } from "@/components/jobs/tailor-cta";
import { CompanyMark } from "@/components/company-mark";
import { JobDescription } from "@/components/job-description";
import { fetchJob, MANUAL_SOURCE } from "@/lib/api";
import { formatRelative, workModelLabel } from "@/lib/utils";

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
  return { title: `${job.title} at ${job.company}`, description: plainDescription };
}

/** Pretty source label, e.g. "greenhouse" → "Greenhouse", manual → "Curated". */
function sourceLabel(source: string): string {
  if (source === MANUAL_SOURCE) return "Curated";
  return source.charAt(0).toUpperCase() + source.slice(1);
}

export default async function JobDetailPage({ params }: { params: { id: string } }) {
  const id = Number(params.id);
  if (!Number.isFinite(id) || id < 1) notFound();

  const job = await fetchJob(id);
  if (!job) notFound();

  const wm = workModelLabel(job.work_model);
  const subParts = [
    job.location || "Location not specified",
    wm,
    `Posted ${formatRelative(job.posted_at ?? job.source_updated_at)}`,
    sourceLabel(job.source),
  ].filter(Boolean);

  return (
    <article className="mx-auto max-w-3xl px-5 py-6 sm:px-8 sm:py-8">
      <div className="mb-4">
        <BackToList />
      </div>

      {/* 1. Header row: company + title (large) with Apply on the right. */}
      <header className="space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <CompanyMark name={job.company} size="lg" />
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-foreground">{job.company}</p>
              <h1 className="text-balance text-2xl font-semibold leading-tight tracking-tight text-foreground sm:text-3xl">
                {job.title}
              </h1>
            </div>
          </div>
          <ApplyButton
            url={job.url}
            size="sm"
            className="hidden shrink-0 rounded-full font-semibold sm:inline-flex"
          >
            Apply
            <ExternalIcon />
          </ApplyButton>
        </div>

        {/* 2. Sub-header meta line. */}
        <p className="text-sm text-muted-foreground">{subParts.join(" · ")}</p>

        {/* Apply button, full width on phones (header button is hidden there). */}
        <ApplyButton url={job.url} className="w-full rounded-full font-semibold sm:hidden">
          Apply at {job.company}
          <ExternalIcon />
        </ApplyButton>
      </header>

      {/* 3. Sponsorship insights placeholder. */}
      <div className="mt-6">
        <SponsorshipInsights />
      </div>

      {/* 4. Tailor CTA — gated action: routes to the ATS Resume Generator
          (/ats/generate?jobId=) with the JD pre-filled and the user's default
          format applied; prompts sign-in otherwise. */}
      <div className="mt-4">
        <TailorCta jobId={job.id} />
      </div>

      <Separator className="my-7" />

      {/* 5. Full JD. */}
      <section aria-labelledby="jd-heading">
        <h2
          id="jd-heading"
          className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted-foreground"
        >
          Job description
        </h2>
        {job.description ? (
          <div className="max-w-prose">
            <JobDescription html={job.description} />
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">No description provided.</p>
        )}
      </section>

      {/* 6. Apply repeated at the bottom. */}
      <div className="mt-8 flex justify-center">
        <ApplyButton url={job.url} size="lg" className="w-full rounded-full font-semibold sm:w-auto">
          Apply at {job.company}
          <ExternalIcon />
        </ApplyButton>
      </div>
    </article>
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
