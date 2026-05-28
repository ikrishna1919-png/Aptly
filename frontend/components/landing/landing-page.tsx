import Link from "next/link";
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

/**
 * Public landing page rendered at `/` for logged-out visitors. The
 * surrounding `/page.tsx` handles the SSR auth check + redirect to
 * `/jobs` for signed-in users — this component is pure presentation.
 *
 * Positioning: the page leads with the visa-sponsorship niche, not a
 * generic "AI job assistant" pitch. Every section is anchored on the
 * specific value for international students / H-1B candidates. The
 * differentiator is repeated three times — hero, feature card,
 * dedicated section — because that's what no broader competitor
 * offers and what the audience is searching for.
 *
 * Honesty constraints (from the design brief):
 *   * Only the three capabilities that actually exist today are
 *     advertised: aggregation from real ATS boards, H-1B signals
 *     from public DOL data, AI resume tailoring.
 *   * No fabricated testimonials or user counts. The trust strip
 *     uses provenance signals ("jobs pulled directly from company
 *     career pages, not scraped aggregators") instead.
 *   * The same DOL-data caveat used inside the app appears here,
 *     verbatim — sponsorship signals don't guarantee any role.
 *
 * Animation: one orchestrated staggered reveal on the hero (handled
 * via the `.reveal-stagger-item` CSS class with per-element delays).
 * Respects `prefers-reduced-motion` — see `globals.css`.
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

// ── Hero ────────────────────────────────────────────────────────────────────

function Hero() {
  return (
    <section className="landing-hero-bg relative overflow-hidden">
      <div className="container py-16 sm:py-24 lg:py-32">
        <div className="mx-auto max-w-3xl text-center">
          <span
            className="reveal-stagger-item inline-flex items-center gap-2 rounded-full border border-border/80 bg-card/80 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground shadow-sm backdrop-blur"
            style={{ animationDelay: "0ms" }}
          >
            <Globe2 className="h-3 w-3 text-primary" aria-hidden />
            For international students & visa holders
          </span>
          <h1
            className="reveal-stagger-item mt-6 font-display text-4xl font-medium leading-[1.05] tracking-tight text-foreground sm:text-5xl md:text-6xl"
            style={{ animationDelay: "100ms" }}
          >
            Find the jobs that{" "}
            <span className="italic text-primary">actually sponsor</span> your
            visa — and tailor your resume to land them.
          </h1>
          <p
            className="reveal-stagger-item mx-auto mt-6 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg"
            style={{ animationDelay: "200ms" }}
          >
            Aptly pulls real openings straight from company career pages and
            flags which employers have a track record of H-1B sponsorship —
            so you spend evenings tailoring resumes for roles you can
            actually land, not chasing dead ends.
          </p>
          <div
            className="reveal-stagger-item mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row"
            style={{ animationDelay: "300ms" }}
          >
            <Button
              asChild
              size="lg"
              className="rounded-full px-6 text-base font-semibold shadow-sm"
            >
              <Link href="/sign-in">
                Get started — sign in with Google
                <ArrowRight className="ml-2 h-4 w-4" aria-hidden />
              </Link>
            </Button>
            <Button
              asChild
              variant="outline"
              size="lg"
              className="rounded-full border-border/80 px-5 text-base font-medium"
            >
              <Link href="/jobs">Browse jobs first</Link>
            </Button>
          </div>
          <p
            className="reveal-stagger-item mt-4 text-xs text-muted-foreground"
            style={{ animationDelay: "400ms" }}
          >
            Free while in early access · No credit card · Google sign-in
          </p>
        </div>
      </div>
    </section>
  );
}

// ── Trust strip ────────────────────────────────────────────────────────────

// Honest provenance signals in place of fake testimonials / user counts.
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
    <section className="border-y border-border/60 bg-card/40">
      <div className="container py-8 sm:py-10">
        <ul className="grid gap-4 sm:grid-cols-3">
          {TRUST_SIGNALS.map((sig, i) => (
            <li
              key={i}
              className="flex items-start gap-3 text-sm leading-relaxed text-muted-foreground"
            >
              <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-secondary text-primary">
                <sig.icon className="h-3.5 w-3.5" aria-hidden />
              </span>
              <span>{sig.text}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
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
    <section className="container py-20 sm:py-28">
      <div className="mx-auto max-w-2xl text-center">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
          How it works
        </p>
        <h2 className="mt-3 font-display text-3xl font-medium tracking-tight sm:text-4xl">
          From feed to filed application in three steps.
        </h2>
      </div>

      <ol className="mx-auto mt-14 grid max-w-5xl gap-6 md:grid-cols-3">
        {STEPS.map((step, i) => (
          <li
            key={step.n}
            className="relative rounded-2xl border border-border/70 bg-card p-7 shadow-card"
          >
            <span className="font-display text-5xl font-medium leading-none text-primary/30">
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
          </li>
        ))}
      </ol>
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
    <section className="bg-card/40 py-20 sm:py-24">
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            What you get today
          </p>
          <h2 className="mt-3 font-display text-3xl font-medium tracking-tight sm:text-4xl">
            Three honest tools. No fluff.
          </h2>
          <p className="mt-3 text-sm text-muted-foreground sm:text-base">
            We only ship what works. Auto-apply, mock interviews, and cover
            letters aren&apos;t here — yet.
          </p>
        </div>

        <div className="mx-auto mt-12 grid max-w-5xl gap-5 md:grid-cols-3">
          {FEATURES.map((f) => (
            <article
              key={f.title}
              className="group rounded-2xl border border-border/70 bg-background p-6 transition-shadow hover:shadow-card-hover"
            >
              <span className="inline-flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
                <f.icon className="h-5 w-5" aria-hidden />
              </span>
              <h3 className="mt-5 text-lg font-semibold tracking-tight text-foreground">
                {f.title}
              </h3>
              <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                {f.body}
              </p>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

// ── Differentiator — H-1B intelligence ────────────────────────────────────

function Differentiator() {
  return (
    <section className="container py-20 sm:py-28">
      <div className="mx-auto grid max-w-5xl items-center gap-12 lg:grid-cols-2">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-primary">
            The differentiator
          </p>
          <h2 className="mt-3 font-display text-3xl font-medium leading-tight tracking-tight sm:text-4xl">
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
        </div>

        <SignalsMock />
      </div>
    </section>
  );
}

/** A static visual mock of the in-app sponsorship badges. NOT a live
 * card — just a representative example using the same visual
 * language as the real `JobCard`. Conveys the value of the feature
 * without faking data. */
function SignalsMock() {
  return (
    <div className="relative">
      <div
        className="absolute inset-0 -z-10 translate-x-3 translate-y-3 rounded-2xl border border-border/50 bg-card/60"
        aria-hidden
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
          <p className="text-sm font-medium text-foreground">
            Example, Inc.
          </p>
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
    <section className="border-t border-border/60 bg-card/40">
      <div className="container py-20 text-center sm:py-24">
        <h2 className="mx-auto max-w-2xl font-display text-3xl font-medium leading-tight tracking-tight sm:text-4xl">
          Spend evenings on the jobs that can{" "}
          <span className="italic text-primary">actually</span> hire you.
        </h2>
        <p className="mx-auto mt-4 max-w-xl text-base text-muted-foreground">
          Sign in with Google. We&apos;ll pull the freshest sponsorship-
          friendly jobs the moment you land.
        </p>
        <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
          <Button
            asChild
            size="lg"
            className="rounded-full px-6 text-base font-semibold shadow-sm"
          >
            <Link href="/sign-in">
              Get started — sign in with Google
              <ArrowRight className="ml-2 h-4 w-4" aria-hidden />
            </Link>
          </Button>
          <Button
            asChild
            variant="outline"
            size="lg"
            className="rounded-full border-border/80 px-5 text-base font-medium"
          >
            <Link href="/jobs">Browse jobs first</Link>
          </Button>
        </div>
      </div>
    </section>
  );
}
