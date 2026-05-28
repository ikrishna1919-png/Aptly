"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { ChevronLeft } from "lucide-react";

/**
 * Mobile-only "back to list" chevron for the job detail view. Preserves the
 * active filters by carrying the current query string back to `/jobs`.
 */
export function BackToList() {
  const params = useSearchParams();
  const qs = params.toString();
  return (
    <Link
      href={qs ? `/jobs?${qs}` : "/jobs"}
      scroll={false}
      className="inline-flex items-center gap-1 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground lg:hidden"
    >
      <ChevronLeft className="h-4 w-4" aria-hidden />
      All jobs
    </Link>
  );
}
