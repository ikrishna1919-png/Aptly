"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Logo } from "@/components/logo";

export function SiteFooter() {
  // The landing route (`/`) is a self-contained full-viewport experience with
  // its own minimal "© Aptly" footer line, so the global footer steps aside
  // there. Only `/` is affected; every other route keeps the standard footer.
  const pathname = usePathname() || "/";
  if (pathname === "/") return null;

  return (
    <footer className="border-t border-border/60 bg-background">
      <div className="container flex flex-col gap-8 py-12 text-sm text-muted-foreground sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-md space-y-3">
          <Logo wordmarkClassName="text-lg" />
          <p className="text-xs leading-relaxed">
            The job search, built for international students who need visa
            sponsorship. Find tech roles from employers with a track record
            of sponsoring, and tailor your resume and cover letter to each
            one — grounded in public ATS boards and public DOL filings.
          </p>
        </div>

        <nav
          aria-label="Footer"
          className="flex flex-col gap-2 text-xs sm:items-end"
        >
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 sm:justify-end">
            <Link href="/?login=1" className="hover:text-foreground">
              Get started
            </Link>
            <Link href="/about" className="hover:text-foreground">
              About
            </Link>
            <Link href="/support" className="hover:text-foreground">
              Support
            </Link>
            <Link href="/?login=1" className="hover:text-foreground">
              Sign in
            </Link>
          </div>
          <span className="text-muted-foreground/70">
            Jobs sourced from Greenhouse · Lever · Ashby · SmartRecruiters · Workday
          </span>
        </nav>
      </div>
    </footer>
  );
}
