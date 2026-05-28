"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState, useTransition } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

type FilterValue = string | boolean | null;

const REMOTE_OPTIONS = [
  { label: "Remote", value: "true" as const },
  { label: "On-site", value: "false" as const },
] as const;

const TYPE_OPTIONS = ["Full-time", "Part-time", "Contract", "Intern"] as const;

const SKILL_SUGGESTIONS = [
  "Python",
  "TypeScript",
  "React",
  "Go",
  "Rust",
  "AWS",
  "Kubernetes",
  "SQL",
] as const;

// How long we wait after the last keystroke before pushing the search
// query into the URL. Short enough that the user perceives the filter
// as live; long enough that we don't fire a request per character.
const SEARCH_DEBOUNCE_MS = 300;

/**
 * Filter bar for the job list. Two interaction patterns:
 *   * Text search + location: debounced auto-apply on input. No
 *     "Apply" button — the filter feels live.
 *   * Chip filters (workplace, type, visa, H-1B signals, skills):
 *     toggle-on-click, push to the URL immediately.
 *
 * On phone-width viewports the chip panel collapses behind a single
 * "Filters" toggle so the screen isn't dominated by filter UI when
 * the user just wants to scroll the feed.
 */
export function JobFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  const [q, setQ] = useState(params.get("q") ?? "");
  // Re-sync the input when the URL changes from somewhere else (e.g.
  // clicking a skill chip rewrites `q`). The debounce-driven commit
  // below is `q`-only, so this useEffect doesn't fight itself.
  useEffect(() => setQ(params.get("q") ?? ""), [params]);

  const [location, setLocation] = useState(params.get("location") ?? "");
  useEffect(() => setLocation(params.get("location") ?? ""), [params]);

  // Mobile-only collapse state. Defaults closed on small screens so
  // the page opens with the feed prominent rather than a tall filter
  // panel; desktop always shows the panel inline.
  const [mobileOpen, setMobileOpen] = useState(false);

  const remote = params.get("remote");
  const employmentType = params.get("employment_type");
  const sponsors = params.get("sponsors_visa");
  const sponsorsH1b = params.get("sponsors_h1b");
  const pastH1b = params.get("past_h1b_activity");
  const skill = params.get("q"); // skill chips piggyback on the q param for now

  const activeCount = useMemo(() => {
    let n = 0;
    if (remote !== null) n++;
    if (employmentType) n++;
    if (sponsors === "true") n++;
    if (sponsorsH1b === "true") n++;
    if (pastH1b === "true") n++;
    if (location) n++;
    if (params.get("q")) n++;
    return n;
  }, [remote, employmentType, sponsors, sponsorsH1b, pastH1b, location, params]);

  // Debounced commit for text inputs. Keep refs so the timeout
  // survives re-renders + can be cancelled when a new keystroke
  // lands.
  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  function commitDebounced(update: Record<string, FilterValue>) {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => commit(update), SEARCH_DEBOUNCE_MS);
  }
  useEffect(() => {
    return () => {
      if (debounceTimer.current) clearTimeout(debounceTimer.current);
    };
  }, []);

  function commit(update: Record<string, FilterValue>) {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(update)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, String(value));
    }
    next.delete("offset");
    startTransition(() => router.push(`/jobs?${next.toString()}`));
  }

  return (
    <section
      aria-label="Filter jobs"
      className={cn(
        "rounded-xl border border-border/70 bg-card/80 shadow-card backdrop-blur",
        pending && "opacity-90",
      )}
    >
      {/* Always-visible search row. Auto-applies on debounce — no
          submit button — but pressing Enter still commits immediately
          for users who'd rather not wait. */}
      <form
        className="flex flex-col gap-2 p-3 sm:flex-row sm:items-stretch sm:p-4"
        onSubmit={(e) => {
          e.preventDefault();
          commit({ q: q.trim() || null, location: location.trim() || null });
        }}
        role="search"
      >
        <div className="relative flex-1">
          <SearchIcon />
          <Input
            placeholder="Search by role, company, or skill"
            value={q}
            onChange={(e) => {
              const value = e.target.value;
              setQ(value);
              commitDebounced({ q: value.trim() || null });
            }}
            className="pl-9"
            aria-label="Search jobs"
          />
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="Location"
            value={location}
            onChange={(e) => {
              const value = e.target.value;
              setLocation(value);
              commitDebounced({ location: value.trim() || null });
            }}
            className="sm:max-w-[200px]"
            aria-label="Location"
          />
          {/* Mobile-only toggle for the chip panel. Desktop hides it
              and shows the panel inline. */}
          <button
            type="button"
            onClick={() => setMobileOpen((v) => !v)}
            aria-expanded={mobileOpen}
            aria-controls="job-filter-chips"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:hidden",
            )}
          >
            <FilterIcon />
            Filters
            {activeCount > 0 && (
              <span className="rounded-full bg-primary px-1.5 text-[10px] font-semibold text-primary-foreground">
                {activeCount}
              </span>
            )}
          </button>
        </div>
      </form>

      <div
        id="job-filter-chips"
        className={cn(
          "border-t border-border/60 p-3 sm:p-4",
          // Hidden on mobile until the user opens the panel;
          // always shown from sm: up.
          mobileOpen ? "block" : "hidden sm:block",
        )}
      >
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <FilterGroup label="Workplace">
            {REMOTE_OPTIONS.map((opt) => (
              <Chip
                key={opt.value}
                active={remote === opt.value}
                onClick={() => commit({ remote: remote === opt.value ? null : opt.value })}
              >
                {opt.label}
              </Chip>
            ))}
          </FilterGroup>

          <FilterGroup label="Type">
            {TYPE_OPTIONS.map((t) => (
              <Chip
                key={t}
                active={employmentType?.toLowerCase() === t.toLowerCase()}
                onClick={() =>
                  commit({
                    employment_type:
                      employmentType?.toLowerCase() === t.toLowerCase() ? null : t,
                  })
                }
              >
                {t}
              </Chip>
            ))}
          </FilterGroup>

          <FilterGroup label="Visa">
            <Chip
              active={sponsors === "true"}
              onClick={() => commit({ sponsors_visa: sponsors === "true" ? null : true })}
            >
              Sponsors visa
            </Chip>
          </FilterGroup>

          {/*
           * The two H-1B chips are distinct on purpose — "Sponsors H-1B"
           * is the conservative high-confidence signal (≥5 LCAs in the
           * last 12 months); "Past H-1B activity" is the inclusive one
           * (any LCA in the last 3 years). Both can be active; the
           * backend treats `false` as 'no filter', so untoggling clears
           * the URL param rather than sending an opposite-sense filter.
           * Both chips wear the primary accent when active to read as
           * the "high-signal" filter group.
           */}
          <FilterGroup label="H-1B (DOL)">
            <Chip
              active={sponsorsH1b === "true"}
              onClick={() =>
                commit({ sponsors_h1b: sponsorsH1b === "true" ? null : true })
              }
              variant="accent"
            >
              Sponsors H-1B
            </Chip>
            <Chip
              active={pastH1b === "true"}
              onClick={() =>
                commit({ past_h1b_activity: pastH1b === "true" ? null : true })
              }
              variant="accent"
            >
              Past H-1B activity
            </Chip>
          </FilterGroup>

          <FilterGroup label="Skills">
            {SKILL_SUGGESTIONS.map((s) => (
              <Chip
                key={s}
                active={skill?.toLowerCase() === s.toLowerCase()}
                onClick={() =>
                  commit({ q: skill?.toLowerCase() === s.toLowerCase() ? null : s })
                }
              >
                {s}
              </Chip>
            ))}
          </FilterGroup>

          {activeCount > 0 && (
            <button
              type="button"
              onClick={() => startTransition(() => router.push("/jobs"))}
              className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              Clear all
              <Badge variant="muted">{activeCount}</Badge>
            </button>
          )}
        </div>

        {/* Disclaimer on the DOL-derived signals. Surfaced near the
            filter chips AND under the badges on the job card so the
            user always has the context to read the signal correctly.
            Misleading users about sponsorship is the core risk of
            this feature — we'd rather be loud about the uncertainty. */}
        <p className="mt-3 text-[11px] leading-relaxed text-muted-foreground">
          H-1B signals reflect public DOL LCA filings from the past 1–3 years.
          The data is incomplete, employer-name mismatches happen, and a signal
          does not guarantee sponsorship for any specific role.
        </p>
      </div>
    </section>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
      <div className="flex flex-wrap gap-1">{children}</div>
    </div>
  );
}

function Chip({
  active,
  onClick,
  children,
  variant = "default",
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
  variant?: "default" | "accent";
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-2.5 py-1 text-[11px] font-medium leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        active
          ? variant === "accent"
            ? "border-primary bg-primary text-primary-foreground shadow-sm"
            : "border-primary/40 bg-primary/10 text-primary"
          : "border-border bg-background text-muted-foreground hover:bg-secondary hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function SearchIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground"
    >
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  );
}

function FilterIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 5h18M6 12h12M10 19h4" />
    </svg>
  );
}
