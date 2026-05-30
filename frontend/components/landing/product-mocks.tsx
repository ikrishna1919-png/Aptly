"use client";

import { useEffect, useState } from "react";
import { useReducedMotion } from "motion/react";
import { Bookmark, MapPin, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";

/**
 * Mocked Aptly job card — the visual centerpiece of Panel 1. Styled from the
 * existing design system so it reads as a real product surface. The
 * "Sponsorship signal — coming soon" chip is the honest placeholder required
 * by CLAUDE.md (LCA ingestion isn't live yet). The company shown is an
 * illustrative example of the kind of employer Aptly surfaces, not a claim
 * about a specific live listing.
 */
export function JobCardMock() {
  return (
    <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-5 shadow-card">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="grid h-11 w-11 place-items-center rounded-xl bg-primary-soft text-base font-bold text-primary-soft-foreground">
            D
          </div>
          <div>
            <p className="font-semibold leading-tight text-card-foreground">Datadog</p>
            <p className="text-sm text-muted-foreground">Backend Engineer</p>
          </div>
        </div>
        <Bookmark className="h-5 w-5 shrink-0 text-muted-foreground/60" aria-hidden />
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        <Badge variant="secondary">Remote</Badge>
        <Badge variant="secondary">Full-time</Badge>
        <Badge variant="secondary">Python</Badge>
      </div>

      <div className="mt-3 inline-flex items-center gap-1.5 rounded-full border border-highlight/30 bg-highlight-soft px-2.5 py-1 text-xs font-medium text-highlight-foreground">
        <Sparkles className="h-3 w-3" aria-hidden />
        Sponsorship signal — coming soon
      </div>

      <div className="mt-4 flex items-center justify-between border-t border-border/60 pt-3 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1">
          <MapPin className="h-3.5 w-3.5" aria-hidden />
          San Francisco, CA
        </span>
        <span>2h ago</span>
      </div>
    </div>
  );
}

// Generic, clearly-mocked résumé bullets. No real metrics about a real
// person — illustrative of the tailored-resume output shape only.
const RESUME_BULLETS = [
  "Engineered Python ETL pipelines processing 20M+ records, reducing latency by 35%.",
  "Led migration to event-driven architecture, cutting incident rate by 40%.",
];

/**
 * Mocked tailored-résumé preview — the visual centerpiece of Panel 2. The
 * name is redacted ("Your Name"); bullets type in one at a time and loop.
 * Honors `prefers-reduced-motion` by showing the full text statically.
 */
export function ResumeMock() {
  const reduce = useReducedMotion();
  return (
    <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-6 shadow-card">
      <div className="border-b border-border/60 pb-4 text-center">
        <p className="text-lg font-bold tracking-tight text-card-foreground">Your Name</p>
        <p className="mt-0.5 text-sm text-muted-foreground">Senior Backend Engineer</p>
        <div className="mt-2 flex justify-center gap-1.5" aria-hidden>
          {[40, 56, 32].map((w, i) => (
            <span
              key={i}
              className="h-1.5 rounded-full bg-muted-foreground/25"
              style={{ width: w }}
            />
          ))}
        </div>
      </div>

      <p className="mt-4 text-[11px] font-semibold uppercase tracking-wider text-primary-soft-foreground">
        Experience
      </p>
      <p className="mt-1 text-sm font-medium text-card-foreground">Backend Engineer · Acme</p>
      <TypewriterBullets lines={RESUME_BULLETS} reduce={!!reduce} />
    </div>
  );
}

function TypewriterBullets({ lines, reduce }: { lines: string[]; reduce: boolean }) {
  // `i` = which bullet is active (lines.length means "all done, pausing");
  // `n` = characters revealed of the active bullet.
  const [i, setI] = useState(0);
  const [n, setN] = useState(0);

  useEffect(() => {
    if (reduce) return;
    if (i >= lines.length) {
      // All bullets typed — hold, then loop from the top.
      const t = setTimeout(() => {
        setI(0);
        setN(0);
      }, 2600);
      return () => clearTimeout(t);
    }
    const full = lines[i];
    if (n < full.length) {
      const t = setTimeout(() => setN((c) => c + 1), 30);
      return () => clearTimeout(t);
    }
    // Bullet finished — short gap before the next one.
    const t = setTimeout(() => {
      setI((c) => c + 1);
      setN(0);
    }, 1400);
    return () => clearTimeout(t);
  }, [i, n, reduce, lines]);

  return (
    <ul className="mt-2 space-y-2" aria-label="Example tailored resume bullets">
      {lines.map((line, idx) => {
        const text = reduce || idx < i ? line : idx === i ? line.slice(0, n) : "";
        if (!text && !reduce && idx !== i) return null;
        const typing = !reduce && idx === i && n < line.length;
        return (
          <li key={idx} className="flex gap-2 text-sm leading-relaxed text-foreground/85">
            <span aria-hidden className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
            <span>
              {text}
              {typing && (
                <span className="ml-0.5 inline-block h-4 w-[2px] -translate-y-[1px] animate-pulse bg-primary align-middle" />
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
