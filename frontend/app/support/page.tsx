import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const SUPPORT_EMAIL = "hello@aptly.fyi";

const FAQS: { q: string; a: string }[] = [
  {
    q: "How does Aptly know which jobs sponsor visas?",
    a: "Two signals. (1) Every job is pulled directly from the company's own ATS — Greenhouse, Lever, Ashby, SmartRecruiters, Workday — so the listing itself is current. (2) We cross-check each employer against the public DOL H-1B LCA disclosure record and surface two distinct badges per company: a conservative one (≥5 LCAs in the past 12 months) and an inclusive one (any LCA in the past 3 years). DOL data is incomplete and employer-name mismatches happen, so a badge is signal, not a guarantee.",
  },
  {
    q: "Does the AI rewrite my resume from scratch?",
    a: "No. The tailoring step starts from YOUR profile and reframes bullets against the job description's keywords, never invents experience or metrics. If the JD wants a skill you haven't confirmed, the analyzer asks you a yes/no question first; only affirmatives flow into the output. The exported DOCX is two pages, ATS-clean, single-column.",
  },
  {
    q: "Why are some features marked 'coming soon'?",
    a: "We ship one feature at a time, only when it actually works end-to-end. Application Tracker, Interview Prep, ATS Checker, and Email Finder are designed and described but not shipped yet. The honest placeholder beats a half-working tool that wastes your time.",
  },
  {
    q: "Where does my profile data go?",
    a: "Your profile lives in Aptly's Postgres database, scoped to your account. The resume-tailoring step sends the job description + your profile to Anthropic's Claude API to do the rewrite; nothing about you is sold or shared with third parties beyond what's needed to run that one call.",
  },
  {
    q: "I signed in with the wrong Google account — how do I switch?",
    a: "Click your avatar in the top-right, then Sign Out. The next 'Sign in with Google' click shows the Google account chooser; pick the right account there. We pass prompt=select_account on every sign-in so the chooser always appears.",
  },
  {
    q: "I can't sign back in after signing out.",
    a: "This was a real bug on the older proxied setup; the session cookie is now scoped to .aptly.fyi and the logout endpoint deletes it cleanly on the same scope. If you still see this, try a hard refresh (Cmd-Shift-R / Ctrl-Shift-R) — and tell us at the email below.",
  },
];

export default function SupportPage() {
  return (
    <main className="container max-w-3xl space-y-10 py-12 sm:py-16">
      <header className="space-y-3">
        <Badge
          variant="outline"
          className="border-primary/30 bg-primary/5 text-xs font-medium uppercase tracking-[0.16em] text-primary"
        >
          Support
        </Badge>
        <h1 className="font-display text-3xl font-medium tracking-tight text-foreground sm:text-4xl">
          Help, FAQs, and how to reach us.
        </h1>
        <p className="max-w-2xl text-base leading-relaxed text-muted-foreground">
          Aptly is early — small team, real users, plenty of rough edges. If
          something&apos;s wrong or missing, tell us; we read every message.
        </p>
      </header>

      <section aria-label="Frequently asked questions" className="space-y-4">
        <h2 className="font-display text-xl font-medium tracking-tight">
          Frequently asked
        </h2>
        <div className="space-y-3">
          {FAQS.map((item) => (
            <details
              key={item.q}
              className="group rounded-xl border border-border/70 bg-card p-5 [&_summary::-webkit-details-marker]:hidden"
            >
              <summary className="flex cursor-pointer list-none items-start justify-between gap-4 text-base font-medium text-foreground">
                <span>{item.q}</span>
                <span
                  aria-hidden="true"
                  className="mt-1 inline-block h-5 w-5 shrink-0 rounded-full border border-border text-center text-sm leading-none text-muted-foreground transition-transform group-open:rotate-45"
                >
                  +
                </span>
              </summary>
              <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
                {item.a}
              </p>
            </details>
          ))}
        </div>
      </section>

      <section aria-label="Contact us">
        <Card className="border-border/70 shadow-sm">
          <CardHeader>
            <CardTitle className="font-display text-xl font-medium tracking-tight">
              Get in touch
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 text-sm text-muted-foreground">
            <p>
              Bug, feature request, or feedback — the fastest path is email:
            </p>
            <p>
              <a
                href={`mailto:${SUPPORT_EMAIL}?subject=Aptly%20support`}
                className="inline-flex items-center rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Email {SUPPORT_EMAIL}
              </a>
            </p>
            <p className="text-xs">
              Include the page you were on and what you expected to happen.
              Screenshots help. We&apos;ll write back.
            </p>
            <p className="pt-2 text-xs">
              You can also{" "}
              <Link
                href="/settings/contact"
                className="font-medium text-primary underline-offset-4 hover:underline"
              >
                use the contact form
              </Link>{" "}
              if you&apos;d rather not leave your inbox.
            </p>
          </CardContent>
        </Card>
      </section>
    </main>
  );
}
