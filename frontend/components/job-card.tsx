import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CompanyMark } from "@/components/company-mark";
import type { Job } from "@/lib/api";
import { MANUAL_SOURCE } from "@/lib/api";
import { cn, formatRelative } from "@/lib/utils";

const MAX_SKILL_CHIPS = 6;

/**
 * The list-page job card. Visual hierarchy, top to bottom:
 *   1. Sponsorship signals (the differentiator — pulled OUT of the
 *      generic meta-badge strip below and given their own row so a
 *      sponsor-friendly job is immediately recognisable on scroll).
 *   2. Company + title, with the title second-line per the
 *      ATS-listing convention.
 *   3. Location, employment-type, remote, salary, "Sponsors visa".
 *   4. Skill chips (truncated at MAX_SKILL_CHIPS).
 *
 * The whole card is clickable via the absolute-positioned link
 * overlay. A tiny chevron in the upper-right makes the affordance
 * explicit on hover — the previous version relied entirely on the
 * shadow shift, which wasn't obviously interactive.
 */
export function JobCard({ job }: { job: Job }) {
  const hasH1bSignal = job.sponsors_h1b || job.past_h1b_activity;
  return (
    <Card
      className={cn(
        "group relative flex flex-col gap-3 p-5 transition-all duration-150",
        "hover:-translate-y-0.5 hover:border-border hover:shadow-card-hover",
      )}
    >
      <Link
        href={`/jobs/${job.id}`}
        className="absolute inset-0 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-label={`${job.title} at ${job.company}`}
      />

      {/* Sponsorship row — promoted ABOVE the company/title block so
          it's the first thing a sponsorship-seeking user sees. Hidden
          entirely when neither H-1B signal fires, so the layout stays
          clean for the no-sponsorship case. */}
      {hasH1bSignal && (
        <div className="relative flex flex-wrap items-center gap-1.5">
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
        </div>
      )}

      <div className="relative flex items-start gap-3">
        <CompanyMark name={job.company} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium text-foreground">
            {job.company}
          </p>
          <h3 className="mt-0.5 truncate text-lg font-semibold leading-tight tracking-tight text-foreground">
            {job.title}
          </h3>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <time
            dateTime={job.source_updated_at ?? undefined}
            className="whitespace-nowrap text-xs text-muted-foreground"
          >
            {formatRelative(job.source_updated_at)}
          </time>
          {/* Click affordance — a tiny chevron that only appears on
              hover. Hidden on touch devices where hover is meaningless. */}
          <ChevronRight className="hidden text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 sm:block" />
        </div>
      </div>

      <div className="relative flex flex-wrap gap-1.5">
        {job.location && (
          <Badge variant="secondary">
            <LocationIcon />
            <span className="truncate max-w-[16ch]">{job.location}</span>
          </Badge>
        )}
        {job.remote === true && <Badge variant="default">Remote</Badge>}
        {job.remote === false && <Badge variant="outline">On-site</Badge>}
        {job.employment_type && (
          <Badge variant="outline">{job.employment_type}</Badge>
        )}
        {job.salary && <Badge variant="highlight">{job.salary}</Badge>}
        {job.sponsors_visa === true && (
          <Badge variant="highlight">Sponsors visa</Badge>
        )}
        {job.source === MANUAL_SOURCE && (
          <Badge variant="muted" title="Added manually by an admin">
            Curated
          </Badge>
        )}
      </div>

      {job.skills.length > 0 && (
        <div className="relative flex flex-wrap gap-1">
          {job.skills.slice(0, MAX_SKILL_CHIPS).map((s) => (
            <span
              key={s}
              className="rounded-md bg-secondary/70 px-1.5 py-0.5 text-[11px] font-medium text-secondary-foreground"
            >
              {s}
            </span>
          ))}
          {job.skills.length > MAX_SKILL_CHIPS && (
            <span className="text-[11px] text-muted-foreground">
              +{job.skills.length - MAX_SKILL_CHIPS}
            </span>
          )}
        </div>
      )}
    </Card>
  );
}

function LocationIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="11"
      height="11"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
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

function SparkleIcon() {
  // Used inside the prominent "Sponsors H-1B" badge to make it stand
  // out further from the lower-weight badges in the meta row.
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

function ChevronRight({ className }: { className?: string }) {
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
      className={className}
    >
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}
