import Link from "next/link";

import { Badge } from "@/components/ui/badge";

export const metadata = {
  title: "About",
  description:
    "Why Aptly exists, who it's for, and where it's headed — the job search built for international students who need visa sponsorship.",
};

// Live today vs. on the roadmap. Kept in one place so the page reads
// as an honest, intentional product picture — never advertising an
// unbuilt feature as shipped.
const AVAILABLE_NOW = [
  {
    title: "Jobs that sponsor, aggregated",
    body: "Live tech postings from real ATS boards (Greenhouse, Lever, Ashby, SmartRecruiters, Workday), each tagged with a sponsorship signal from public DOL data.",
  },
  {
    title: "AI resume & cover-letter tailoring",
    body: "Tailor your resume and draft a cover letter for any role — ATS-clean and grounded in your real experience, never invented.",
  },
];

const COMING_SOON = [
  "Job alerts when a sponsoring employer posts a fitting role",
  "Apply in fewer clicks via a browser companion (you stay in control)",
  "Reach the right recruiter or hiring contact, built to respect privacy",
  "Interview prep with a sponsorship lens",
  "An application tracker so your pipeline never goes stale",
  "Deeper sponsorship intelligence from public DOL/LCA data",
];

export default function AboutPage() {
  return (
    <main className="container max-w-3xl space-y-10 py-12 sm:py-16">
      <header className="space-y-3">
        <Badge
          variant="outline"
          className="border-primary/30 bg-primary/5 text-xs font-medium uppercase tracking-[0.16em] text-primary"
        >
          About
        </Badge>
        <h1 className="font-display text-3xl font-medium tracking-tight text-foreground sm:text-4xl md:text-[2.75rem]">
          Built for the people whose job search has a visa attached.
        </h1>
      </header>

      <section className="space-y-5 text-base leading-relaxed text-muted-foreground">
        <p>
          Aptly is a job-search platform for international students and
          early-career workers who need visa (H-1B) sponsorship to work in
          the US. It exists because finding the few companies that actually
          sponsor — and getting your resume read before an ATS bins it — is a
          job in itself, layered on top of the real one.
        </p>
        <p>
          We pull live postings directly from real ATS boards (Greenhouse,
          Lever, Ashby, SmartRecruiters, Workday), cross-check each employer
          against the public DOL H-1B LCA disclosure data, and surface that
          sponsorship signal alongside the listing — so you can spot the
          companies actually hiring international talent before you spend an
          evening tailoring an application.
        </p>
        <p>
          The AI tailoring runs on Anthropic Claude. It rewrites your resume
          and drafts a cover letter against a specific job&apos;s keyword
          profile WITHOUT inventing experience you don&apos;t have — when the
          JD asks for something missing, the analyzer asks you a yes/no
          question first, and only your affirmative answers flow into the
          output. ATS-clean, two pages, single column.
        </p>
      </section>

      <section className="space-y-4">
        <h2 className="font-display text-xl font-medium tracking-tight text-foreground">
          What works today
        </h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {AVAILABLE_NOW.map((f) => (
            <div
              key={f.title}
              className="rounded-2xl border border-border/70 bg-card p-5"
            >
              <div className="flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success/10 px-2.5 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] text-success">
                  <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-success" />
                  Available now
                </span>
              </div>
              <h3 className="mt-3 text-base font-semibold tracking-tight text-foreground">
                {f.title}
              </h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                {f.body}
              </p>
            </div>
          ))}
        </div>
      </section>

      <section className="space-y-4">
        <h2 className="font-display text-xl font-medium tracking-tight text-foreground">
          Where we&apos;re headed
        </h2>
        <p className="text-sm leading-relaxed text-muted-foreground">
          The vision is to handle the whole sponsorship-aware job search end
          to end. These are on the roadmap — clearly labelled, because we ship
          one feature at a time, only when it works:
        </p>
        <ul className="grid gap-2.5 sm:grid-cols-2">
          {COMING_SOON.map((item) => (
            <li
              key={item}
              className="flex items-start gap-2.5 rounded-xl border border-dashed border-border bg-card/50 p-3.5 text-sm leading-relaxed text-muted-foreground"
            >
              <span className="mt-0.5 inline-flex shrink-0 items-center rounded-full border border-highlight/40 bg-highlight-soft px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.1em] text-highlight-foreground">
                Soon
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="space-y-4 rounded-2xl border border-border/70 bg-card p-6 sm:p-8">
        <h2 className="font-display text-xl font-medium tracking-tight text-foreground">
          The honest small print
        </h2>
        <ul className="space-y-3 text-sm leading-relaxed text-muted-foreground">
          <li className="flex gap-3">
            <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
            <span>
              Sponsorship signals come from public DOL LCA filings from the
              past 1–3 years. Data is incomplete, employer-name mismatches
              happen, and a signal does NOT guarantee sponsorship for any
              specific role.
            </span>
          </li>
          <li className="flex gap-3">
            <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
            <span>
              We never scrape LinkedIn, Indeed, or Glassdoor. We work from
              public ATS APIs and public government data only.
            </span>
          </li>
          <li className="flex gap-3">
            <span aria-hidden="true" className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
            <span>
              We don&apos;t do hands-off &quot;auto-apply.&quot; Preparing
              applications is great; submitting them without the candidate&apos;s
              eyes on them is not. You stay in the loop.
            </span>
          </li>
        </ul>
      </section>

      <section className="space-y-4">
        <h2 className="font-display text-xl font-medium tracking-tight">
          Mission
        </h2>
        <p className="text-base leading-relaxed text-muted-foreground">
          Make the international job search predictable. Reduce the time
          between &quot;I&apos;d apply if I knew this company sponsors&quot; and
          &quot;the recruiter is reading my application&quot; from weeks of
          research to an evening of focused work.
        </p>
      </section>

      <footer className="rounded-xl border border-border/70 bg-card p-5 text-sm text-muted-foreground">
        Questions, feedback, or a bug to report?{" "}
        <Link href="/support" className="font-medium text-primary underline-offset-4 hover:underline">
          Reach the team via Support
        </Link>
        .
      </footer>
    </main>
  );
}
