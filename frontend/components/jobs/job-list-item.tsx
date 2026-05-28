"use client";

import Link from "next/link";
import { forwardRef } from "react";

import { SponsorshipPill } from "@/components/jobs/sponsorship";
import type { Job } from "@/lib/api";
import { cn, formatRelative, stripTags, workModelLabel } from "@/lib/utils";

/**
 * Left-pane job card. Visual hierarchy, top to bottom (per spec):
 *   1. Sponsorship pill (mandatory anchor on every card).
 *   2. Company (bold, small) + posted-time (right).
 *   3. Title (larger, bold).
 *   4. Location · work model.
 *   5. One-line JD snippet (~120 chars).
 *
 * The whole card is a link to `/jobs/[id]`. Because the list lives in the
 * jobs layout (which persists across the child route change), clicking only
 * swaps the detail pane — the list never remounts. `selected` draws the
 * left-accent + tint; `?next` could be layered later. `preserveQuery` keeps
 * the active filters in the URL when selecting.
 */
export const JobListItem = forwardRef<
  HTMLAnchorElement,
  { job: Job; selected: boolean; search: string }
>(function JobListItem({ job, selected, search }, ref) {
  const posted = formatRelative(job.posted_at ?? job.source_updated_at);
  const wm = workModelLabel(job.work_model);
  const snippet = stripTags(job.description).slice(0, 120);
  const href = `/jobs/${job.id}${search ? `?${search}` : ""}`;

  return (
    <Link
      ref={ref}
      href={href}
      scroll={false}
      data-job-id={job.id}
      aria-current={selected ? "true" : undefined}
      className={cn(
        "block rounded-xl border bg-card p-4 transition-all duration-150",
        "hover:-translate-y-0.5 hover:shadow-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-l-[3px] border-l-primary border-border bg-primary-soft/40 shadow-card"
          : "border-border/70 hover:border-border",
      )}
    >
      <SponsorshipPill />

      <div className="mt-2.5 flex items-baseline justify-between gap-3">
        <p className="truncate text-sm font-semibold text-foreground">{job.company}</p>
        <span className="shrink-0 text-xs text-muted-foreground">{posted}</span>
      </div>

      <h3 className="mt-0.5 text-base font-semibold leading-snug tracking-tight text-foreground">
        {job.title}
      </h3>

      <p className="mt-1 text-xs text-muted-foreground">
        {[job.location || "Location not specified", wm].filter(Boolean).join(" · ")}
      </p>

      {snippet && (
        <p className="mt-2 truncate text-xs leading-relaxed text-muted-foreground/90">
          {snippet}
          {job.description && stripTags(job.description).length > 120 ? "…" : ""}
        </p>
      )}
    </Link>
  );
});
