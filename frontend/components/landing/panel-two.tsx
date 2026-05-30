"use client";

import { motion, useReducedMotion } from "motion/react";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";

import { GradientBackdrop } from "./gradient-backdrop";
import { ResumeMock } from "./product-mocks";

/**
 * Panel 2 — "The Differentiator". Honest copy: aggregation direct from ATS
 * feeds (no scraping), tailoring grounded in real experience, and
 * sponsorship intelligence named explicitly as coming-soon. `onGetStarted`
 * opens the login modal; `onBack` returns to Panel 1 (never trap the user).
 */
export function PanelTwo({
  onGetStarted,
  onBack,
}: {
  onGetStarted: () => void;
  onBack: () => void;
}) {
  const reduce = useReducedMotion();

  return (
    <div className="relative flex h-full w-full items-center overflow-hidden">
      <GradientBackdrop variant="two" />

      <div className="container relative z-10 grid items-center gap-10 py-12 md:grid-cols-2 md:gap-8">
        {/* Text column */}
        <div className="max-w-xl">
          <span className="inline-flex items-center rounded-full border border-primary/20 bg-primary-soft px-3 py-1 text-xs font-medium text-primary-soft-foreground">
            Built different.
          </span>

          <h1 className="mt-4 font-display text-4xl font-bold leading-[1.08] tracking-tight text-foreground sm:text-5xl">
            Sponsorship intelligence + honest tailoring.
          </h1>

          <p className="mt-5 max-w-md text-base text-muted-foreground sm:text-lg">
            Aptly aggregates tech jobs directly from company ATS feeds — no scraping, no
            aggregator middlemen. AI tailors your resume to each role, grounded in your
            real experience. Sponsorship intelligence from DOL/LCA public data — coming
            soon.
          </p>

          <div className="mt-7 flex flex-col items-start gap-3">
            <Button size="lg" className="font-semibold" onClick={onGetStarted}>
              Get Started
            </Button>
            <button
              type="button"
              onClick={onBack}
              className="inline-flex items-center gap-1 text-sm text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
              Back
            </button>
          </div>
        </div>

        {/* Mock column */}
        <div className="flex justify-center md:justify-end">
          <motion.div
            initial={reduce ? false : { opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: reduce ? 0 : 0.15, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
          >
            <ResumeMock />
          </motion.div>
        </div>
      </div>
    </div>
  );
}
