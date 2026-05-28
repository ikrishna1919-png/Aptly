"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { JobsFilterBar } from "@/components/jobs/jobs-filter-bar";
import { JobListItem } from "@/components/jobs/job-list-item";
import { fetchJobs, type Job, type JobsQuery } from "@/lib/api";
import { cn } from "@/lib/utils";

// Cap the list at a readable size; the feed window is small today. If a
// window ever exceeds this we surface a note (and react-window is the
// flagged follow-up).
const LIST_LIMIT = 100;

function queryFromParams(params: URLSearchParams): JobsQuery {
  const q: JobsQuery = { limit: LIST_LIMIT };
  const text = params.get("q");
  if (text) q.q = text;
  const location = params.get("location");
  if (location) q.location = location;
  const jobType = params.get("job_type");
  if (jobType) q.job_type = jobType;
  const pw = params.get("posted_within");
  if (pw) q.posted_within = pw;
  return q;
}

/**
 * Split-pane master/detail shell for `/jobs` and `/jobs/[id]`.
 *
 * Lives in the jobs LAYOUT, so the list + filters persist across the child
 * route change — selecting a job only swaps the right pane (`children`),
 * the list never remounts or refetches. Desktop: 40/60 columns, each
 * scrolling independently under a sticky filter bar. Mobile: list-only on
 * `/jobs`, full-screen detail on `/jobs/[id]` (slides in).
 */
export function JobsShell({ children }: { children: ReactNode }) {
  const pathname = usePathname() || "/jobs";
  const params = useSearchParams();
  const router = useRouter();
  const reduced = useReducedMotion();

  const selectedId = useMemo(() => {
    const m = pathname.match(/^\/jobs\/(\d+)/);
    return m ? Number(m[1]) : null;
  }, [pathname]);

  const search = params.toString();
  // Re-fetch only when the filter params change (not on selection change).
  const queryKey = useMemo(() => {
    const p = new URLSearchParams();
    for (const k of ["q", "location", "job_type", "posted_within"]) {
      const v = params.get(k);
      if (v) p.set(k, v);
    }
    return p.toString();
  }, [params]);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "error">("loading");

  useEffect(() => {
    let cancelled = false;
    setState("loading");
    setTotal(null);
    fetchJobs(queryFromParams(new URLSearchParams(queryKey)))
      .then((data) => {
        if (cancelled) return;
        setJobs(data.jobs);
        setTotal(data.total);
        setState("ok");
      })
      .catch(() => {
        if (cancelled) return;
        setState("error");
      });
    return () => {
      cancelled = true;
    };
  }, [queryKey]);

  const listRef = useRef<HTMLDivElement | null>(null);
  const detailRef = useRef<HTMLDivElement | null>(null);

  // Scroll the selected card into view (covers a direct visit to /jobs/[id]
  // and keyboard navigation).
  useEffect(() => {
    if (selectedId == null || !listRef.current) return;
    const el = listRef.current.querySelector<HTMLElement>(`[data-job-id="${selectedId}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedId, jobs]);

  const go = useCallback(
    (id: number | null) => {
      const qs = search ? `?${search}` : "";
      router.push(id == null ? `/jobs${qs}` : `/jobs/${id}${qs}`, { scroll: false });
    },
    [router, search],
  );

  // Keyboard navigation when focus isn't in a form control.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const tag = (document.activeElement?.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      if (jobs.length === 0) return;
      const idx = selectedId == null ? -1 : jobs.findIndex((j) => j.id === selectedId);

      if (e.key === "ArrowDown" || e.key === "j") {
        e.preventDefault();
        const next = jobs[Math.min(idx + 1, jobs.length - 1)] ?? jobs[0];
        go(next.id);
      } else if (e.key === "ArrowUp" || e.key === "k") {
        e.preventDefault();
        const prev = jobs[Math.max(idx - 1, 0)] ?? jobs[0];
        go(prev.id);
      } else if (e.key === "Enter" && selectedId != null) {
        e.preventDefault();
        detailRef.current?.focus();
      } else if (e.key === "Escape" && selectedId != null) {
        e.preventDefault();
        go(null);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [jobs, selectedId, go]);

  const detailVariants = reduced
    ? { initial: { opacity: 0 }, animate: { opacity: 1 }, exit: { opacity: 0 } }
    : {
        // Mobile slides from the right; desktop is a quiet fade/swap. We use
        // x for both but keep it tiny on desktop via the transition.
        initial: { opacity: 0, x: 24 },
        animate: { opacity: 1, x: 0 },
        exit: { opacity: 0, x: 12 },
      };

  return (
    <div className="flex h-[calc(100dvh-3.5rem)] flex-col sm:h-[calc(100dvh-4rem)]">
      <JobsFilterBar total={total} />

      <div className="flex-1 overflow-hidden lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        {/* Left: job list. Hidden on mobile when a job is selected. */}
        <div
          ref={listRef}
          className={cn(
            "h-full overflow-y-auto px-4 py-4 sm:px-6 lg:border-r lg:border-border/60",
            selectedId != null ? "hidden lg:block" : "block",
          )}
        >
          <JobListPane jobs={jobs} state={state} total={total} selectedId={selectedId} search={search} />
        </div>

        {/* Right: detail pane (children = empty state or job detail). */}
        <div
          ref={detailRef}
          tabIndex={-1}
          className={cn(
            "h-full overflow-y-auto focus:outline-none",
            selectedId != null ? "block" : "hidden lg:block",
          )}
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.div
              key={selectedId ?? "empty"}
              variants={detailVariants}
              initial="initial"
              animate="animate"
              exit="exit"
              transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
              className="h-full"
            >
              {children}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

function JobListPane({
  jobs,
  state,
  total,
  selectedId,
  search,
}: {
  jobs: Job[];
  state: "loading" | "ok" | "error";
  total: number | null;
  selectedId: number | null;
  search: string;
}) {
  if (state === "loading") {
    return (
      <ul className="space-y-3" aria-busy="true">
        {Array.from({ length: 6 }).map((_, i) => (
          <li key={i} className="h-[116px] animate-pulse rounded-xl border border-border/60 bg-muted/40" />
        ))}
      </ul>
    );
  }
  if (state === "error") {
    return (
      <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
        Couldn&apos;t load jobs. Check your connection and try again.
      </p>
    );
  }
  if (jobs.length === 0) {
    return (
      <div className="rounded-xl border border-dashed border-border/70 bg-card/50 p-6 text-center">
        <p className="text-sm font-medium text-foreground">No jobs match your filters</p>
        <p className="mt-1 text-xs text-muted-foreground">
          Try widening the location, clearing a filter, or checking back later.
        </p>
      </div>
    );
  }
  return (
    <>
      <ul className="space-y-3">
        {jobs.map((job) => (
          <li key={job.id}>
            <JobListItem job={job} selected={job.id === selectedId} search={search} />
          </li>
        ))}
      </ul>
      {total != null && total > jobs.length && (
        <p className="mt-4 text-center text-xs text-muted-foreground">
          Showing the {jobs.length} most recent of {total}. Narrow with filters to see more.
        </p>
      )}
    </>
  );
}
