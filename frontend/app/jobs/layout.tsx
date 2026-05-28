"use client";

import type { ReactNode } from "react";

import { JobsShell } from "@/components/jobs/jobs-shell";
import { RequireProfile } from "@/lib/auth-context";

/**
 * Gates every `/jobs/**` route behind a real, saved profile, then renders
 * the split-pane shell. The shell (filters + job list) lives HERE rather
 * than in the page so it persists across `/jobs` ↔ `/jobs/[id]` — selecting
 * a job only swaps the detail pane (`children`); the list never remounts.
 */
export default function JobsLayout({ children }: { children: ReactNode }) {
  return (
    <RequireProfile>
      <JobsShell>{children}</JobsShell>
    </RequireProfile>
  );
}
