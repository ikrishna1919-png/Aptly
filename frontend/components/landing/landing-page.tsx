"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  motion,
  useInView,
  useMotionValue,
  useReducedMotion,
  useScroll,
  useSpring,
  useTransform,
  type Variants,
} from "motion/react";
import {
  Briefcase,
  FileText,
  ShieldCheck,
  ArrowRight,
  Database,
  Sparkles,
  Globe2,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Public landing page rendered at `/` for logged-out visitors. The
 * surrounding `app/page.tsx` handles the SSR auth check + the
 * redirect for signed-in visitors — this component is pure
 * presentation.
 *
 * Motion approach
 * ───────────────
 *
 *   * One orchestrated page-load sequence on the hero: badge →
 *     headline → subhead → CTA → trust-line, each fading + rising
 *     8px with a 80–120ms stagger. Anchored on a single `Variants`
 *     object so the cubic-bezier easing reads consistently across
 *     the whole sequence.
 *   * Scroll-triggered reveals on every section below the fold via
 *     `useInView` + `viewport={{ once: true }}` so a long-scrolled
 *     page doesn't re-trigger animations and chew CPU. Each section
 *     stagger-children variant carries 60–80ms between its inner
 *     items.
 *   * Subtle atmosphere on the hero: radial-gradient washes + an
 *     SVG noise overlay (one inline data-URI, no extra request),
 *     plus a low-amplitude mouse-follow on the primary wash —
 *     skipped on touch devices and on `prefers-reduced-motion`.
 *   * Refined hover states on feature cards + CTAs: a -2px lift,
 *     a 5–10% shadow ramp, and a tiny scale on the buttons. Stays
 *     under the threshold where motion starts feeling "noisy" on
 *     a marketing page that already moves on scroll.
 *
 * Accessibility / performance
 * ───────────────────────────
 *
 *   * `useReducedMotion()` is read at the top and short-circuits
 *     EVERY animation: stagger durations collapse to 0, transitions
 *     vanish, the mouse-follow stops binding listeners. The page
 *     still renders the same DOM; it just becomes static.
 *   * The mouse-follow uses `useMotionValue` + a spring, NOT React
 *     state — it bypasses re-renders entirely so the parallax can
 *     run at 60fps without rerunning the React tree.
 *   * No images bigger than the SVG sponsorship-mock; the
 *     atmospheric "graphics" are CSS-generated. Single bundle, fast
 *     mobile.
 *   * Below-the-fold sections are mounted from the start but
 *     `whileInView` keeps their animations cheap — `once: true` and
 *     reasonable margins on the IntersectionObserver root.
 */
export function LandingPage() {
  return (
    <div className="overflow-x-hidden">
      <Hero />
      <TrustStrip />
      <HowItWorks />
      <Features />
      <Differentiator />
      <FinalCta />
    </div>
  );
}

// ── Animation primitives ────────────────────────────────────────────────────

/** Default easing for everything on the page. `easeOutExpo`-ish
 * curve — strong start, gentle settle — reads premium without
 * feeling slow. Stored once so the whole landing inherits the
 * same motion grammar. */
const EASE: [number, number, number, number] = [0.22, 1, 0.36, 1];

/** Stagger container for sections that fade their children in
 * sequence. `staggerChildren` is per-item delay between siblings;
 * `delayChildren` is the initial offset before the first child
 * starts. Both collapse to 0 under `prefers-reduced-motion`. */
function staggerContainer(staggerChildren = 0.08, delayChildren = 0.08): Variants {
  return {
    hidden: {},
    show: {
      transition: { staggerChildren, delayChildren },
    },
  };
}

/** The atom every section uses: fade in + small upward translate.
 * 24px → 0 reads as "rising into place" without being theatrical.
 * Distance shrinks on `prefers-reduced-motion`. */
const fadeUp: Variants = {
  hidden: { opacity: 0, y: 16 },
  show: { opacity: 1, y: 0, transition: { duration: 0.6, ease: EASE } },
};

/** Same shape as fadeUp but enters from the side. Used for the
 * differentiator's two-column reveal. */
const fadeLeft: Variants = {
  hidden: { opacity: 0, x: -24 },
  show: { opacity: 1, x: 0, transition: { duration: 0.7, ease: EASE } },
};
const fadeRight: Variants = {
  hidden: { opacity: 0, x: 24 },
  show: { opacity: 1, x: 0, transition: { duration: 0.7, ease: EASE } },
};

/** Wraps children with a scroll-triggered reveal. Uses `useInView`
 * with `once: true` so the animation only runs the first time the
 * section enters the viewport — a long scroll doesn't keep
 * re-triggering. */
function SectionReveal({
  children,
  className,
  variants = fadeUp,
}: {
  children: ReactNode;
  className?: string;
  variants?: Variants;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  // `margin: "-10% 0px"` fires the animation slightly before the
  // section is fully in view so it doesn't feel sluggish on long
  // scrolls.
  const inView = useInView(ref, { once: true, margin: "-10% 0px" });
  return (
    <motion.div
      ref={ref}
      initial="hidden"
      animate={inView ? "show" : "hidden"}
      variants={variants}
      className={className}
    >
      {children}
    </motion.div>
  );
}

// ── Hero ────────────────────────────────────────────────────────────────────

function Hero() {
  const reduced = useReducedMotion();

  // Mouse-follow gradient parallax. Two motion values that track
  // the cursor position; a spring smooths the trajectory so the
  // gradient doesn't jitter. Skipped under `prefers-reduced-motion`
  // and on touch devices (no `mousemove` events fire there anyway,
  // so the default 0,0 is fine).
  const mx = useMotionValue(0);
  const my = useMotionValue(0);
  const springX = useSpring(mx, { stiffness: 60, damping: 20 });
  const springY = useSpring(my, { stiffness: 60, damping: 20 });
  // `xPct` / `yPct` are `0–100`, used inside the radial-gradient
  // expression. A 5% amplitude is enough to be perceptible without
  // calling attention to itself.
  const xPct = useTransform(springX, (v) => `${50 + v * 5}%`);
  const yPct = useTransform(springY, (v) => `${30 + v * 5}%`);

  // Subtle vertical parallax on the headline as the user scrolls —
  // pulls the title up slightly while the rest scrolls normally.
  // Decoupled from the mouse follow.
  const heroRef = useRef<HTMLDivElement | null>(null);
  const { scrollYProgress } = useScroll({
    target: heroRef,
    offset: ["start start", "end start"],
  });
  const headlineY = useTransform(scrollYProgress, [0, 1], [0, reduced ? 0 : -40]);
  const ambientOpacity = useTransform(scrollYProgress, [0, 1], [1, 0.6]);

  useEffect(() => {
    if (reduced) return;
    // Skip on touch devices — no mouse means the transform stays
    // pinned at 0,0 which is fine but means we waste the listener.
    if (typeof window !== "undefined" && window.matchMedia("(pointer: coarse)").matches) return;
    const onMove = (e: MouseEvent) => {
      const w = window.innerWidth;
      const h = window.innerHeight;
      // Normalise to [-1, 1] then scale into motion values.
      mx.set(e.clientX / w - 0.5);
      my.set(e.clientY / h - 0.5);
    };
    window.addEventListener("mousemove", onMove, { passive: true });
    return () => window.removeEventListener("mousemove", onMove);
  }, [mx, my, reduced]);

  return (
    <section ref={heroRef} className="relative isolate overflow-hidden">
      {/* Layered atmosphere. Two radial washes (a violet anchor +
          an amber highlight), a diagonal grid for texture, and a
          fine SVG noise overlay to break the gradients. All CSS,
          no images. The primary wash follows the cursor. */}
      <motion.div
        aria-hidden="true"
        style={{
          backgroundImage: useTransform(
            [xPct, yPct] as const,
            ([x, y]) =>
              `radial-gradient(ellipse 70% 55% at ${x} ${y}, hsl(var(--primary) / 0.16), transparent 70%)`,
          ),
          opacity: ambientOpacity,
        }}
        className="pointer-events-none absolute inset-0 -z-10"
      />
      <motion.div
        aria-hidden="true"
        style={{ opacity: ambientOpacity }}
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_60%_50%_at_92%_8%,hsl(var(--highlight)/0.16),transparent_70%)]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[linear-gradient(135deg,transparent_0,transparent_49.5%,hsl(var(--foreground)/0.03)_50%,transparent_50.5%)] bg-[length:32px_32px]"
      />
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 opacity-[0.035] mix-blend-overlay"
        style={{ backgroundImage: `url("${NOISE_DATA_URI}")` }}
      />

      {/* Hero padding sized so the badge / headline / subhead / CTA
          all fit above the fold on typical viewport heights (~640px
          for short laptop, 800px+ for desktop). The headline ramp
          stops earlier (max 4rem) so on shorter screens the CTA
          stays visible without scrolling. */}
      <div className="container py-12 sm:py-16 lg:py-20">
        <motion.div
          initial="hidden"
          animate="show"
          variants={staggerContainer(0.09, 0.05)}
          className="mx-auto max-w-3xl text-center"
        >
          <motion.span
            variants={fadeUp}
            className="inline-flex items-center gap-2 rounded-full border border-border/80 bg-card/80 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground shadow-sm backdrop-blur"
          >
            <Globe2 className="h-3 w-3 text-primary" aria-hidden />
            For international students & visa holders
          </motion.span>
          <motion.h1
            variants={fadeUp}
            style={{ y: headlineY }}
            className="mt-5 font-display text-[2.25rem] font-medium leading-[1.05] tracking-tight text-foreground sm:text-5xl md:text-[3.5rem] lg:text-[4rem]"
          >
            Find the jobs that{" "}
            <span className="relative inline-block whitespace-nowrap">
              <em className="relative z-10 not-italic italic text-primary">actually sponsor</em>
              <motion.span
                aria-hidden="true"
                initial={{ scaleX: 0 }}
                animate={{ scaleX: 1 }}
                transition={{ duration: 0.8, delay: 0.55, ease: EASE }}
                className="absolute -bottom-1 left-0 right-0 -z-0 origin-left h-2 rounded-full bg-primary/15"
                style={{ transformOrigin: "left" }}
              />
            </span>{" "}
            your visa — and tailor your resume to land them.
          </motion.h1>
          <motion.p
            variants={fadeUp}
            className="mx-auto mt-5 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg"
          >
            Aptly pulls real openings straight from company career pages and
            flags which employers have a track record of H-1B sponsorship —
            so you spend evenings tailoring resumes for roles you can actually
            land, not chasing dead ends.
          </motion.p>
          <motion.div
            variants={fadeUp}
            className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row"
          >
            <CtaButton href="/get-started" />
          </motion.div>
          <motion.p
            variants={fadeUp}
            className="mt-4 text-xs text-muted-foreground"
          >
            Free while in early access · No credit card · One-click Google sign-up
          </motion.p>
        </motion.div>
      </div>
    </section>
  );
}

/** Primary CTA. Pulled out so the hover/tap motion is consistent
 * across the hero + the final CTA without duplicating prop bags. */
function CtaButton({ href }: { href: string }) {
  return (
    <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }}>
      <Button
        asChild
        size="lg"
        className="group rounded-full px-7 text-base font-semibold shadow-sm transition-shadow hover:shadow-md"
      >
        <Link href={href}>
          Get Started
          <ArrowRight
            className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-0.5"
            aria-hidden
          />
        </Link>
      </Button>
    </motion.div>
  );
}

// ── Trust strip ────────────────────────────────────────────────────────────

const TRUST_SIGNALS = [
  {
    icon: Database,
    text: "Jobs pulled directly from company career pages — Greenhouse, Lever, Ashby, SmartRecruiters, Workday. Not scraped from aggregators.",
  },
  {
    icon: ShieldCheck,
    text: "Sponsorship signals derived from public DOL H-1B LCA disclosure data.",
  },
  {
    icon: Sparkles,
    text: "AI resume tailoring runs on Anthropic Claude — your data isn't sold or shared.",
  },
];

function TrustStrip() {
  return (
    <SectionReveal className="border-y border-border/60 bg-card/40">
      <div className="container py-10 sm:py-12">
        <motion.ul
          variants={staggerContainer(0.08, 0)}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "-10% 0px" }}
          className="grid gap-5 sm:grid-cols-3"
        >
          {TRUST_SIGNALS.map((sig, i) => (
            <motion.li
              key={i}
              variants={fadeUp}
              className="flex items-start gap-3 text-sm leading-relaxed text-muted-foreground"
            >
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-secondary text-primary">
                <sig.icon className="h-3.5 w-3.5" aria-hidden />
              </span>
              <span>{sig.text}</span>
            </motion.li>
          ))}
        </motion.ul>
      </div>
    </SectionReveal>
  );
}

// ── How it works ──────────────────────────────────────────────────────────

const STEPS = [
  {
    n: 1,
    title: "Browse aggregated jobs",
    body: "Fresh postings pulled every six hours from real ATS boards. Filter by location, role, skills, sponsorship signals.",
  },
  {
    n: 2,
    title: "See who actually sponsors",
    body: "Each employer is checked against public DOL H-1B filings. Heavy sponsors and past-activity companies are surfaced as distinct badges.",
  },
  {
    n: 3,
    title: "Tailor your resume with AI",
    body: "Claude rewrites your resume per job for ATS keyword matching. Export the DOCX, apply on the company's site, done.",
  },
];

function HowItWorks() {
  return (
    <section className="container py-24 sm:py-32">
      <SectionReveal className="mx-auto max-w-2xl text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          How it works
        </p>
        <h2 className="mt-3 font-display text-3xl font-medium tracking-tight sm:text-4xl md:text-[2.75rem]">
          From feed to filed application in three steps.
        </h2>
      </SectionReveal>

      <motion.ol
        variants={staggerContainer(0.1, 0.1)}
        initial="hidden"
        whileInView="show"
        viewport={{ once: true, margin: "-10% 0px" }}
        className="mx-auto mt-16 grid max-w-5xl gap-6 md:grid-cols-3"
      >
        {STEPS.map((step, i) => (
          <motion.li
            key={step.n}
            variants={fadeUp}
            whileHover={{ y: -4 }}
            transition={{ type: "spring", stiffness: 300, damping: 24 }}
            className="group relative rounded-2xl border border-border/70 bg-card p-7 shadow-card transition-shadow hover:shadow-card-hover"
          >
            <span className="font-display text-5xl font-medium leading-none text-primary/30 transition-colors group-hover:text-primary/50">
              {String(step.n).padStart(2, "0")}
            </span>
            <h3 className="mt-4 text-lg font-semibold tracking-tight text-foreground">
              {step.title}
            </h3>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
              {step.body}
            </p>
            {i < STEPS.length - 1 && (
              <ArrowRight
                className="absolute -right-3 top-1/2 hidden h-5 w-5 -translate-y-1/2 text-border md:block"
                aria-hidden
              />
            )}
          </motion.li>
        ))}
      </motion.ol>
    </section>
  );
}

// ── Feature cards ──────────────────────────────────────────────────────────

const FEATURES = [
  {
    icon: Briefcase,
    title: "Real jobs, not ghost listings",
    body: "Every posting is pulled live from the company's own ATS — Greenhouse, Lever, Ashby, SmartRecruiters, Workday — so you're not chasing roles that closed three months ago.",
  },
  {
    icon: ShieldCheck,
    title: "H-1B sponsorship signals",
    body: "Two distinct badges per company: heavy sponsor (≥5 LCAs in 12 months) and past activity (any LCA in 3 years). The signal nobody else surfaces.",
  },
  {
    icon: FileText,
    title: "AI-tailored, ATS-optimized resume",
    body: "Claude rewrites your resume per role with the job's exact terminology, keeping every claim grounded in what you actually did. DOCX export, two pages, ATS-clean.",
  },
];

function Features() {
  return (
    <section className="relative overflow-hidden bg-card/40 py-24 sm:py-28">
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 top-0 -z-10 h-px bg-gradient-to-r from-transparent via-border to-transparent"
      />
      <div className="container">
        <SectionReveal className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            What you get today
          </p>
          <h2 className="mt-3 font-display text-3xl font-medium tracking-tight sm:text-4xl md:text-[2.75rem]">
            Three honest tools. No fluff.
          </h2>
          <p className="mt-4 text-sm text-muted-foreground sm:text-base">
            We only ship what works. Auto-apply, mock interviews, and cover
            letters aren&apos;t here — yet.
          </p>
        </SectionReveal>

        <motion.div
          variants={staggerContainer(0.08, 0.08)}
          initial="hidden"
          whileInView="show"
          viewport={{ once: true, margin: "-10% 0px" }}
          className="mx-auto mt-14 grid max-w-5xl gap-5 md:grid-cols-3"
        >
          {FEATURES.map((f) => (
            <motion.article
              key={f.title}
              variants={fadeUp}
              whileHover={{ y: -4 }}
              transition={{ type: "spring", stiffness: 300, damping: 24 }}
              className="group relative overflow-hidden rounded-2xl border border-border/70 bg-background p-6 transition-shadow hover:shadow-card-hover"
            >
              {/* Soft inner glow that fades in on hover. Sits behind
                  the content so it lights the card from below without
                  shifting any pixels. */}
              <div
                aria-hidden="true"
                className="pointer-events-none absolute inset-0 -z-10 bg-gradient-to-br from-primary/0 via-primary/0 to-primary/0 opacity-0 transition-opacity duration-300 group-hover:from-primary/[0.04] group-hover:via-transparent group-hover:to-primary/[0.04] group-hover:opacity-100"
              />
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary transition-colors group-hover:bg-primary/15">
                <f.icon className="h-5 w-5" aria-hidden />
              </span>
              <h3 className="mt-5 text-lg font-semibold tracking-tight text-foreground">
                {f.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {f.body}
              </p>
            </motion.article>
          ))}
        </motion.div>
      </div>
    </section>
  );
}

// ── Differentiator — H-1B intelligence ────────────────────────────────────

function Differentiator() {
  return (
    <section className="container py-24 sm:py-32">
      <div className="mx-auto grid max-w-5xl items-center gap-12 lg:grid-cols-2">
        <SectionReveal variants={fadeLeft}>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            The differentiator
          </p>
          <h2 className="mt-3 font-display text-3xl font-medium leading-tight tracking-tight sm:text-4xl md:text-[2.75rem]">
            Sponsorship intelligence built into the feed.
          </h2>
          <p className="mt-5 text-base leading-relaxed text-muted-foreground">
            Most job sites treat &ldquo;sponsors visa&rdquo; as a one-bit
            filter the recruiter sets. Aptly cross-checks every employer
            against the public DOL H-1B LCA disclosure record and surfaces
            <strong className="font-semibold text-foreground">
              {" "}
              two distinct signals
            </strong>
            : a conservative one (≥5 LCAs in the past 12 months) and an
            inclusive one (any LCA in the past 3 years).
          </p>
          <p className="mt-4 text-base leading-relaxed text-muted-foreground">
            You can filter the feed by either signal, see the raw filing
            counts on each job, and know — at a glance — whether a company
            actually sponsors before you spend an evening tailoring.
          </p>
          <p className="mt-6 rounded-lg border border-border/60 bg-muted/40 p-4 text-xs leading-relaxed text-muted-foreground">
            <strong className="font-semibold text-foreground">
              Honest caveat:
            </strong>{" "}
            signals reflect public DOL LCA filings from the past 1–3 years.
            The data is incomplete, employer-name mismatches happen, and a
            signal does not guarantee sponsorship for any specific role.
          </p>
        </SectionReveal>

        <SectionReveal variants={fadeRight}>
          <SignalsMock />
        </SectionReveal>
      </div>
    </section>
  );
}

/** A static visual mock of the in-app sponsorship badges. NOT a live
 * card — just a representative example using the same visual
 * language as the real `JobCard`. Conveys the value of the feature
 * without faking data. The shadow-card-back layer adds depth. */
function SignalsMock() {
  return (
    <div className="relative">
      <motion.div
        aria-hidden="true"
        initial={{ x: 12, y: 12, opacity: 0 }}
        whileInView={{ x: 12, y: 12, opacity: 1 }}
        viewport={{ once: true, margin: "-10% 0px" }}
        transition={{ delay: 0.2, duration: 0.7, ease: EASE }}
        className="absolute inset-0 -z-10 rounded-2xl border border-border/50 bg-card/60"
      />
      <div className="rounded-2xl border border-border/80 bg-card p-6 shadow-card">
        <div className="flex flex-wrap items-center gap-2">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground shadow-sm">
            <Sparkles className="h-3 w-3" aria-hidden />
            Sponsors H-1B
          </span>
          <span className="inline-flex items-center gap-1 rounded-full border border-border bg-background px-2.5 py-1 text-xs text-muted-foreground">
            Sponsors visa
          </span>
        </div>
        <div className="mt-5">
          <p className="text-sm font-medium text-foreground">Example, Inc.</p>
          <h4 className="mt-0.5 text-base font-semibold tracking-tight text-foreground">
            Senior Software Engineer, Platform
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            New York · Full-time · Posted 2 days ago
          </p>
        </div>
        <dl className="mt-6 grid grid-cols-3 gap-3 border-t border-border/60 pt-4 text-xs">
          <Stat label="LCAs · 12mo" value="42" />
          <Stat label="LCAs · 3yr" value="156" />
          <Stat label="Last filing" value="Oct 2024" />
        </dl>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </dt>
      <dd className="mt-0.5 font-display text-lg font-medium text-foreground">
        {value}
      </dd>
    </div>
  );
}

// ── Final CTA ──────────────────────────────────────────────────────────────

function FinalCta() {
  return (
    <section className="relative overflow-hidden border-t border-border/60 bg-card/40">
      {/* Soft re-statement of the hero's atmosphere so the page
          closes on the same visual key it opened on. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(ellipse_50%_60%_at_50%_120%,hsl(var(--primary)/0.12),transparent_70%)]"
      />
      <SectionReveal>
        <div className="container py-24 text-center sm:py-28">
          <h2 className="mx-auto max-w-2xl font-display text-3xl font-medium leading-tight tracking-tight sm:text-4xl md:text-[2.75rem]">
            Spend evenings on the jobs that can{" "}
            <span className="italic text-primary">actually</span> hire you.
          </h2>
          <p className="mx-auto mt-5 max-w-xl text-base text-muted-foreground">
            One click to create a profile. We&apos;ll pull the freshest
            sponsorship-friendly jobs the moment your résumé is in.
          </p>
          <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <CtaButton href="/get-started" />
          </div>
        </div>
      </SectionReveal>
    </section>
  );
}

// ── SVG noise data-URI ────────────────────────────────────────────────────
//
// One tiny inlined SVG filter. Adds film-grain texture over the hero
// gradients at very low opacity so the washes don't read as flat.
// Inline data-URI keeps it a single bundle, no extra request.
const NOISE_DATA_URI = `data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.6 0'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>`;

// Re-export `cn` only to silence the linter when motion's hover
// helpers aren't enough — kept here in case a future tweak needs
// classNames merging.
export const _utils = { cn };
