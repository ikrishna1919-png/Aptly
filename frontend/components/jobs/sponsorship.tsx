import { Clock } from "lucide-react";

/**
 * Sponsorship placeholders. Until the DOL/LCA ingestion ships we show an
 * honest "coming soon" treatment — never fabricated data. The pill is the
 * mandatory visual anchor on every job card; the insights panel is its
 * detail-pane counterpart. When real data lands, these two components are
 * the only things that change.
 */

/** Small muted pill shown at the top of every job card. */
export function SponsorshipPill() {
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-border/70 bg-muted/60 px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
      title="Sponsorship signals from public DOL/LCA data are coming soon."
    >
      <Clock className="h-3 w-3" aria-hidden />
      Sponsorship data — coming soon
    </span>
  );
}

/** Boxed insights panel for the job detail pane. */
export function SponsorshipInsights() {
  return (
    <section
      aria-label="Sponsorship insights"
      className="rounded-xl border border-primary/15 bg-primary-soft/40 p-4"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 className="text-sm font-semibold text-foreground">Sponsorship insights</h2>
        <span className="inline-flex items-center gap-1.5 rounded-full border border-highlight/40 bg-highlight-soft px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-highlight-foreground">
          <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-highlight" />
          Coming soon
        </span>
      </div>
      <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
        Once our DOL/LCA integration ships, this section will show recent H-1B
        filings, sponsorship history, and salary ranges for this employer.
      </p>
    </section>
  );
}
