import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CompanyMark } from "@/components/company-mark";
import type { Job } from "@/lib/api";
import { MANUAL_SOURCE } from "@/lib/api";
import { cn, formatRelative } from "@/lib/utils";

const MAX_SKILL_CHIPS = 6;

export function JobCard({ job }: { job: Job }) {
  return (
    <Card
      className={cn(
        "group relative flex flex-col gap-3 p-5 transition-shadow hover:shadow-card-hover",
      )}
    >
      <Link
        href={`/jobs/${job.id}`}
        className="absolute inset-0 rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        aria-label={`${job.title} at ${job.company}`}
      />

      <div className="flex items-start gap-3">
        <CompanyMark name={job.company} />
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium uppercase tracking-wider text-muted-foreground">
            {job.company}
          </p>
          <h3 className="mt-0.5 truncate text-base font-semibold leading-tight text-foreground">
            {job.title}
          </h3>
        </div>
        <time
          dateTime={job.source_updated_at ?? undefined}
          className="shrink-0 whitespace-nowrap text-xs text-muted-foreground"
        >
          {formatRelative(job.source_updated_at)}
        </time>
      </div>

      <div className="flex flex-wrap gap-1.5">
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
        {/*
         * H-1B sponsorship signals from public DOL LCA filings. The two
         * badges are deliberately distinct — the conservative one
         * ("Sponsors H-1B") implies a real ongoing pipeline; the
         * inclusive one ("Past H-1B activity") only that the company
         * has at least one filing in the last three years. Both can
         * render; only the inclusive one can render alone. A company
         * with no LCA history gets NEITHER badge — never a "doesn't
         * sponsor" badge.
         */}
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
        {job.source === MANUAL_SOURCE && (
          <Badge variant="muted" title="Added manually by an admin">
            Curated
          </Badge>
        )}
      </div>

      {job.skills.length > 0 && (
        <div className="flex flex-wrap gap-1">
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
