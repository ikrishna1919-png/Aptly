"use client";

import { useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { cn } from "@/lib/utils";

// Exact copy, in order. Card 7 is the resolution and gets a distinct,
// subtly-primary treatment.
const CARDS = [
  "Tired of guessing which companies sponsor?",
  "Tired of writing a fresh resume for every job?",
  "Tired of filling out the same forms over and over?",
  "Tired of applying through five different portals?",
  "Tired of getting silently rejected by ATS systems?",
  "Tired of doing this alone?",
  "Built for international students.",
] as const;

const EASE = [0.22, 1, 0.36, 1] as const;

/**
 * Right-column "playcards": a bordered card that auto-cycles through the 7
 * messages above, with dot indicators. Pauses on hover so users can read.
 * Under `prefers-reduced-motion` it slows to 5s/card and crossfades (no
 * y-slide). The final card reads as a resolution via a primary tint.
 */
export function Playcards() {
  const reduce = useReducedMotion();
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused) return;
    const delay = reduce ? 5000 : 2000;
    const t = setTimeout(() => setIndex((i) => (i + 1) % CARDS.length), delay);
    return () => clearTimeout(t);
  }, [index, paused, reduce]);

  const isFinal = index === CARDS.length - 1;

  return (
    <div
      className="w-full max-w-[500px]"
      onMouseEnter={() => setPaused(true)}
      onMouseLeave={() => setPaused(false)}
    >
      <div
        className={cn(
          "relative flex min-h-[220px] items-center justify-center overflow-hidden rounded-2xl border p-8 text-center shadow-card transition-colors sm:min-h-[260px]",
          isFinal ? "border-primary/30 bg-primary-soft" : "border-border bg-card",
        )}
      >
        <AnimatePresence mode="wait">
          <motion.p
            key={index}
            initial={reduce ? { opacity: 0 } : { opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, y: -20 }}
            transition={{ duration: reduce ? 0.25 : 0.4, ease: EASE }}
            className={cn(
              "text-balance text-2xl font-semibold leading-snug tracking-tight sm:text-[2rem]",
              isFinal ? "text-primary" : "text-foreground",
            )}
          >
            {CARDS[index]}
          </motion.p>
        </AnimatePresence>
      </div>

      {/* Dot indicators */}
      <div className="mt-4 flex items-center justify-center gap-2" aria-hidden>
        {CARDS.map((_, i) => (
          <span
            key={i}
            className={cn(
              "h-1.5 rounded-full transition-all duration-300",
              i === index ? "w-5 bg-primary" : "w-1.5 bg-muted-foreground/30",
            )}
          />
        ))}
      </div>
      <span className="sr-only" aria-live="polite">
        {CARDS[index]}
      </span>
    </div>
  );
}
