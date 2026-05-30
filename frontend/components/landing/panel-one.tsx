"use client";

import { useRef } from "react";
import {
  motion,
  useMotionTemplate,
  useMotionValue,
  useReducedMotion,
  useSpring,
  useTransform,
} from "motion/react";

import { Button } from "@/components/ui/button";

import { GradientBackdrop } from "./gradient-backdrop";
import { JobCardMock } from "./product-mocks";

const HEADLINE = "Job search built for visa-seekers.";

/**
 * Panel 1 — "The Promise". Text left, floating + mouse-tilt job-card mock
 * right (stacked on mobile). `onGetStarted` advances to Panel 2; `onSignIn`
 * opens the existing login modal. `active` gates the headline word-in
 * animation so it (re)plays when the panel is shown.
 */
export function PanelOne({
  onGetStarted,
  onSignIn,
  isMobile,
}: {
  onGetStarted: () => void;
  onSignIn: () => void;
  isMobile: boolean;
}) {
  const reduce = useReducedMotion();
  const panelRef = useRef<HTMLDivElement>(null);

  // Mouse parallax → card tilt (desktop only). Springs smooth the motion.
  const px = useMotionValue(0);
  const py = useMotionValue(0);
  const rotX = useSpring(useTransform(py, [-0.5, 0.5], [6, -6]), { stiffness: 150, damping: 18 });
  const rotY = useSpring(useTransform(px, [-0.5, 0.5], [-6, 6]), { stiffness: 150, damping: 18 });
  const transform = useMotionTemplate`perspective(900px) rotateX(${rotX}deg) rotateY(${rotY}deg)`;

  const parallaxOn = !reduce && !isMobile;

  function onMove(e: React.MouseEvent) {
    if (!parallaxOn || !panelRef.current) return;
    const r = panelRef.current.getBoundingClientRect();
    px.set((e.clientX - r.left) / r.width - 0.5);
    py.set((e.clientY - r.top) / r.height - 0.5);
  }
  function onLeave() {
    px.set(0);
    py.set(0);
  }

  const words = HEADLINE.split(" ");
  const wordStagger = isMobile ? 0.5 / words.length : 0.8 / words.length;

  return (
    <div
      ref={panelRef}
      onMouseMove={onMove}
      onMouseLeave={onLeave}
      className="relative flex h-full w-full items-center overflow-hidden"
    >
      <GradientBackdrop variant="one" />

      <div className="container relative z-10 grid items-center gap-10 py-12 md:grid-cols-2 md:gap-8">
        {/* Text column */}
        <div className="max-w-xl">
          <span className="inline-flex items-center rounded-full border border-primary/20 bg-primary-soft px-3 py-1 text-xs font-medium text-primary-soft-foreground">
            For international students
          </span>

          <h1 className="mt-4 font-display text-4xl font-bold leading-[1.05] tracking-tight text-foreground sm:text-5xl lg:text-6xl">
            {reduce
              ? HEADLINE
              : words.map((w, i) => (
                  <motion.span
                    key={i}
                    className="inline-block"
                    initial={{ opacity: 0, y: "0.3em" }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ delay: i * wordStagger, duration: 0.5, ease: [0.22, 1, 0.36, 1] }}
                  >
                    {w}
                    {i < words.length - 1 ? " " : ""}
                  </motion.span>
                ))}
          </h1>

          <p className="mt-5 max-w-md text-base text-muted-foreground sm:text-lg">
            Tech jobs from companies that sponsor. AI tailoring grounded in your real
            experience. No padded resumes.
          </p>

          <div className="mt-7 flex flex-col items-start gap-3">
            <Button size="lg" className="font-semibold" onClick={onGetStarted}>
              Get Started
            </Button>
            <button
              type="button"
              onClick={onSignIn}
              className="text-sm text-muted-foreground underline-offset-4 transition-colors hover:text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              Already have an account? Sign in
            </button>
          </div>
        </div>

        {/* Mock column */}
        <div className="flex justify-center md:justify-end">
          <motion.div
            animate={reduce ? undefined : { y: [0, -8, 0] }}
            transition={
              reduce ? undefined : { duration: 3, repeat: Infinity, ease: "easeInOut" }
            }
          >
            <motion.div style={parallaxOn ? { transform } : undefined}>
              <JobCardMock />
            </motion.div>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
