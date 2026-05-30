"use client";

import { useCallback, useEffect, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { Logo } from "@/components/logo";
import { useOpenLogin } from "@/lib/use-login-modal";

import { PanelOne } from "./panel-one";
import { PanelTwo } from "./panel-two";

/**
 * Public landing experience at `/` for logged-out visitors — a two-panel,
 * full-viewport, no-scroll sequence. `app/page.tsx` still owns the SSR auth
 * check + redirect for signed-in users; this is pure presentation.
 *
 * Structure: one route, client-side `panel` state (1 | 2). Panel 1 is the
 * promise; "Get Started" animates to Panel 2 (the differentiator); Panel 2's
 * "Get Started" opens the existing login modal (`?login=1`), and "Back"
 * returns to Panel 1. The global SiteHeader/SiteFooter opt out of `/` so this
 * gets a true full-viewport canvas (the panels carry their own minimal
 * Sign-in + "© Aptly" chrome).
 *
 * Motion: AnimatePresence `mode="wait"` swaps panels with a fade + x-drift +
 * scale, direction-aware so "Back" reverses. Everything collapses to an
 * instant crossfade under `prefers-reduced-motion`.
 */

const EASE = [0.22, 1, 0.36, 1] as const;

function useIsMobile() {
  const [mobile, setMobile] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 767px)");
    const sync = () => setMobile(mq.matches);
    sync();
    mq.addEventListener("change", sync);
    return () => mq.removeEventListener("change", sync);
  }, []);
  return mobile;
}

export function LandingPage() {
  const reduce = useReducedMotion();
  const isMobile = useIsMobile();
  const openLogin = useOpenLogin();
  const [panel, setPanel] = useState<1 | 2>(1);
  const [dir, setDir] = useState<1 | -1>(1);

  const goTo = useCallback((p: 1 | 2) => {
    setDir(p > 1 ? 1 : -1);
    setPanel(p);
  }, []);
  const signIn = useCallback(() => openLogin(), [openLogin]);

  const duration = reduce ? 0.2 : isMobile ? 0.45 : 0.6;
  const variants = {
    enter: (d: number) => (reduce ? { opacity: 0 } : { opacity: 0, x: d > 0 ? 60 : -60 }),
    center: { opacity: 1, x: 0, scale: 1 },
    exit: (d: number) =>
      reduce ? { opacity: 0 } : { opacity: 0, x: d > 0 ? -30 : 30, scale: 0.96 },
  };

  return (
    <div className="relative h-dvh w-full overflow-hidden bg-background">
      {/* Minimal top chrome: brand + persistent sign-in escape hatch. */}
      <div className="absolute inset-x-0 top-0 z-30 flex items-center justify-between px-5 py-4 sm:px-8">
        <Logo />
        <button
          type="button"
          onClick={signIn}
          className="rounded-md px-2 py-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          Sign in
        </button>
      </div>

      <AnimatePresence mode="wait" custom={dir} initial={false}>
        <motion.div
          key={panel}
          custom={dir}
          variants={variants}
          initial="enter"
          animate="center"
          exit="exit"
          transition={{ duration, ease: EASE }}
          className="absolute inset-0"
        >
          {panel === 1 ? (
            <PanelOne onGetStarted={() => goTo(2)} onSignIn={signIn} isMobile={isMobile} />
          ) : (
            <PanelTwo onGetStarted={signIn} onBack={() => goTo(1)} />
          )}
        </motion.div>
      </AnimatePresence>

      {/* Minimal footer line (kept to a single row per the brief). */}
      <div className="absolute inset-x-0 bottom-0 z-30 flex items-center justify-center gap-3 px-5 py-3 text-xs text-muted-foreground">
        <span>© Aptly</span>
        <span aria-hidden>·</span>
        <a
          href="/about"
          className="underline-offset-4 transition-colors hover:text-foreground hover:underline"
        >
          About
        </a>
      </div>
    </div>
  );
}
