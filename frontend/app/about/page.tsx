import Link from "next/link";

import { Badge } from "@/components/ui/badge";

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
          Aptly is a job-search tool for international students and H-1B
          candidates in the US. It exists because finding the few companies
          that actually sponsor visas — and getting your résumé read at them
          before the ATS bins it — is a job in itself, layered on top of the
          real job.
        </p>
        <p>
          We pull live postings directly from real ATS boards (Greenhouse,
          Lever, Ashby, SmartRecruiters, Workday), cross-check each employer
          against the public DOL H-1B LCA disclosure data, and surface that
          sponsorship signal alongside the listing — so you can spot the
          companies actually hiring international talent before you spend an
          evening tailoring a résumé.
        </p>
        <p>
          The AI tailoring runs on Anthropic Claude. It rewrites your résumé
          against a specific job&apos;s keyword profile WITHOUT inventing
          experience you don&apos;t have — when the JD asks for something
          missing, the analyzer asks you a yes/no question first, and only
          your affirmative answers flow into the output. ATS-clean DOCX,
          two pages, single column.
        </p>
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
              The &quot;Auto-apply&quot; features common to other sites are NOT a thing
              here. Preparing applications is great; submitting them without
              the candidate&apos;s eyes on them is not. You stay in the loop.
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
          &quot;the recruiter is reading my résumé&quot; from weeks of research
          to an evening of work.
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
