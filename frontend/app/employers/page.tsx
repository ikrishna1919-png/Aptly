import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "For Employers",
  description: "Hire visa-ready international talent on Aptly — coming soon.",
};

/**
 * Minimal "coming soon" stub for the "For Employers" nav link. Honest
 * placeholder per CLAUDE.md — the employer side isn't built yet, so this
 * states that plainly rather than linking to nothing.
 */
export default function EmployersPage() {
  return (
    <div className="container flex min-h-[60vh] flex-col items-center justify-center py-16 text-center">
      <span className="inline-flex items-center rounded-full border border-primary/20 bg-primary-soft px-3 py-1 text-xs font-medium text-primary-soft-foreground">
        Coming soon
      </span>
      <h1 className="mt-4 font-display text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
        For Employers
      </h1>
      <p className="mt-4 max-w-md text-muted-foreground">
        We&apos;re building tools for teams that sponsor international talent — reach
        candidates who are visa-ready and role-matched. It isn&apos;t live yet.
      </p>
      <Link
        href="/"
        className="mt-8 text-sm font-medium text-primary underline-offset-4 hover:underline"
      >
        Back to home
      </Link>
    </div>
  );
}
