import Link from "next/link";
import { FileText, Sparkles, Mail, Settings2, PenLine } from "lucide-react";

// The /ats hub landing: 5 sub-feature cards. Each links to its own route.
// (Generator, cover-letter, and the format/builder pages are separate routes.)
const FEATURES = [
  {
    href: "/ats/format",
    title: "Choose Default Resume Format",
    blurb: "Pick the look your resumes use by default — AI-suggested, matched from an upload, or hand-picked.",
    icon: Settings2,
  },
  {
    href: "/ats/builder",
    title: "Resume Builder",
    blurb: "Build a resume from your profile, import from LinkedIn, or reformat an existing one.",
    icon: FileText,
  },
  {
    href: "/ats/generate",
    title: "ATS Resume Generator",
    blurb: "Tailor your resume to a specific job description, with a real before/after keyword-coverage score.",
    icon: Sparkles,
  },
  {
    href: "/ats/cover-letter",
    title: "ATS Cover Letter Generator",
    blurb: "Generate a grounded cover letter for a job — your real experience only, never invented.",
    icon: Mail,
  },
  {
    href: "/ats/cover-letter-format",
    title: "Choose Default Cover Letter Format",
    blurb: "Set the default style your cover letters use.",
    icon: PenLine,
  },
];

export default function ATSHubPage() {
  return (
    <div className="container max-w-5xl py-12">
      <h1 className="font-display text-3xl font-bold tracking-tight">ATS toolkit</h1>
      <p className="mt-2 max-w-2xl text-muted-foreground">
        Everything for getting past applicant tracking systems — resume formatting, building,
        job-tailored generation, and cover letters.
      </p>
      <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {FEATURES.map((f) => (
          <Link
            key={f.href}
            href={f.href}
            className="group flex flex-col rounded-2xl border border-border bg-card p-6 shadow-card transition-all hover:border-primary/40 hover:shadow-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <f.icon className="h-7 w-7 text-primary" aria-hidden />
            <p className="mt-3 text-lg font-semibold">{f.title}</p>
            <p className="mt-1 flex-1 text-sm text-muted-foreground">{f.blurb}</p>
            <span className="mt-4 inline-flex items-center text-sm font-medium text-primary">
              Open →
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
