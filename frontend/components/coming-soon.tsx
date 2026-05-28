import type { ReactNode } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";

/**
 * Polished placeholder for pages whose feature isn't shipped yet.
 *
 * Two non-negotiables, on purpose:
 *   * No fake data / no non-working buttons that look real. The
 *     visitor leaves understanding exactly what the feature WILL
 *     do, not what it does today.
 *   * Same nav shell + design system as the live pages, so the
 *     surface still feels intentional — not a 404 or an empty
 *     placeholder.
 *
 * Usage:
 *
 *   <ComingSoon
 *     eyebrow="Application Tracker"
 *     title="Track every application end-to-end."
 *     blurb="…"
 *     bullets={["Status board (applied / interviewing / offer / rejected).", "…"]}
 *   />
 */
export function ComingSoon({
  eyebrow,
  title,
  blurb,
  bullets,
  preview,
}: {
  /** Short label above the title — page name or category. */
  eyebrow: string;
  /** One-line lead. */
  title: string;
  /** Two-or-three-sentence description of what the feature will do
   * once shipped. */
  blurb: string;
  /** Bulleted list of concrete capabilities the feature will offer.
   * Three to five entries reads best. */
  bullets: string[];
  /** Optional illustrative preview slot — a static mock of the
   * intended layout. Doesn't function; just gives the visitor a
   * shape for what's coming. */
  preview?: ReactNode;
}) {
  return (
    <main className="container max-w-4xl space-y-8 py-12 sm:py-16">
      <header className="space-y-4">
        {/* Two labels, on purpose: the page/category eyebrow, plus a
            consistent amber "Coming soon" pill that matches the
            landing-page roadmap treatment — so the distinction reads
            the same everywhere. */}
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className="border-primary/30 bg-primary/5 text-xs font-medium uppercase tracking-[0.16em] text-primary"
          >
            {eyebrow}
          </Badge>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-highlight/40 bg-highlight-soft px-2.5 py-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-highlight-foreground">
            <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-highlight" />
            Coming soon
          </span>
        </div>
        <h1 className="font-display text-3xl font-medium tracking-tight text-foreground sm:text-4xl md:text-[2.75rem]">
          {title}
        </h1>
        <p className="max-w-2xl text-base leading-relaxed text-muted-foreground">{blurb}</p>
      </header>

      <section
        aria-label="What this will do"
        className="rounded-2xl border border-border/70 bg-card p-6 shadow-sm sm:p-8"
      >
        <h2 className="font-display text-lg font-medium tracking-tight text-foreground">
          What it&apos;ll do
        </h2>
        <ul className="mt-4 space-y-3 text-sm leading-relaxed text-muted-foreground">
          {bullets.map((b) => (
            <li key={b} className="flex gap-3">
              <span
                aria-hidden="true"
                className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary"
              />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      </section>

      {preview && (
        <section
          aria-label="Preview"
          className="rounded-2xl border border-dashed border-border/70 bg-secondary/30 p-6 sm:p-8"
        >
          <p className="mb-4 text-[11px] font-medium uppercase tracking-[0.16em] text-muted-foreground">
            Preview · not interactive
          </p>
          {preview}
        </section>
      )}

      <footer className="rounded-xl border border-border/70 bg-card p-5 text-sm text-muted-foreground">
        We ship one feature at a time, only when it works.{" "}
        <Link
          href="/jobs"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          Browse the live job feed
        </Link>{" "}
        while you wait — or{" "}
        <Link
          href="/support"
          className="font-medium text-primary underline-offset-4 hover:underline"
        >
          tell us what you need first
        </Link>
        .
      </footer>
    </main>
  );
}
