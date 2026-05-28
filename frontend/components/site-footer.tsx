import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";

export function SiteFooter() {
  return (
    <footer className="border-t border-border/60 bg-background">
      <div className="container flex flex-col gap-6 py-10 text-sm text-muted-foreground sm:flex-row sm:items-start sm:justify-between">
        <div className="max-w-md space-y-2">
          <div className="flex items-center gap-2">
            <BrandMark className="h-5 w-5" />
            <span className="font-display text-base font-medium text-foreground">
              Aptly
            </span>
          </div>
          <p className="text-xs leading-relaxed">
            Jobs that actually sponsor visas — for international students
            and H-1B candidates. Aggregated from public ATS boards;
            sponsorship signals from public DOL filings.
          </p>
        </div>

        <nav
          aria-label="Footer"
          className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs"
        >
          <Link href="/jobs" className="hover:text-foreground">
            Browse jobs
          </Link>
          <Link href="/sign-in" className="hover:text-foreground">
            Sign in
          </Link>
          <span className="text-muted-foreground/70">
            Greenhouse · Lever · Ashby · SmartRecruiters · Workday
          </span>
        </nav>
      </div>
    </footer>
  );
}
