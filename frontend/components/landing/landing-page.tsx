"use client";

import { useRouter } from "next/navigation";
import { useCallback } from "react";
import { motion, useReducedMotion, type Variants } from "motion/react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useOpenLogin } from "@/lib/use-login-modal";

import { LogoMarquee } from "./logo-marquee";
import { Playcards } from "./playcards";

/**
 * Public landing experience at `/` — a single-screen layout that sits under
 * the global top nav (SiteHeader): a hero row (promise text left,
 * auto-cycling "playcards" right), a caption, and a continuous logo marquee
 * of companies in the feed. No vertical scroll at desktop heights; mobile
 * stacks and may scroll.
 *
 * `app/page.tsx` owns the SSR auth check + redirect for signed-in users, so
 * this is effectively the logged-out surface. "Get Started" opens the
 * existing login modal (`?login=1`); if somehow reached while authenticated
 * it routes to /jobs.
 */

const HEADLINE = "Job search, resume optimization, and form-filling — all in one platform.";

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.05, delayChildren: 0.05 } },
};
const word: Variants = {
  hidden: { opacity: 0, y: "0.35em" },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] } },
};

export function LandingPage() {
  const reduce = useReducedMotion();
  const router = useRouter();
  const openLogin = useOpenLogin();
  const { status } = useAuth();

  const getStarted = useCallback(() => {
    if (status === "authenticated") router.push("/jobs");
    else openLogin("/jobs");
  }, [status, router, openLogin]);

  const words = HEADLINE.split(" ");

  return (
    <div className="flex flex-col lg:h-[calc(100svh-4rem)] lg:overflow-hidden">
      {/* Hero row */}
      <section className="flex flex-1 items-center">
        <div className="container grid items-center gap-10 py-10 lg:grid-cols-[55fr_45fr] lg:gap-12 lg:py-0">
          {/* Left: promise */}
          <div className="max-w-2xl">
            <span className="inline-flex items-center rounded-full border border-primary/20 bg-primary-soft px-3 py-1 text-xs font-medium text-primary-soft-foreground">
              For international students
            </span>

            <motion.h1
              variants={reduce ? undefined : container}
              initial={reduce ? undefined : "hidden"}
              animate={reduce ? undefined : "show"}
              className="mt-4 font-display text-4xl font-extrabold leading-[1.08] tracking-tight text-foreground sm:text-5xl lg:text-[3.25rem]"
            >
              {reduce
                ? HEADLINE
                : words.map((w, i) => (
                    <motion.span key={i} variants={word} className="inline-block">
                      {w}
                      {i < words.length - 1 ? " " : ""}
                    </motion.span>
                  ))}
            </motion.h1>

            <p className="mt-5 max-w-xl text-lg text-muted-foreground">
              Built for visa-seeking students applying to tech jobs in the US.
            </p>

            <div className="mt-7">
              <Button size="lg" className="font-semibold" onClick={getStarted}>
                Get Started
              </Button>
            </div>
          </div>

          {/* Right: playcards */}
          <div className="flex justify-center lg:justify-end">
            <Playcards />
          </div>
        </div>
      </section>

      {/* Caption + marquee (pinned to the bottom of the screen on desktop) */}
      <div className="shrink-0 pb-6 pt-2">
        <p className="mb-3 text-center text-base text-muted-foreground sm:text-[17px]">
          Apply for jobs from these companies in seconds
        </p>
        <LogoMarquee />
      </div>
    </div>
  );
}
