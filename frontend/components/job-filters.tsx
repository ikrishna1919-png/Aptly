"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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

export function JobFilters() {
  const router = useRouter();
  const params = useSearchParams();
  const [pending, startTransition] = useTransition();

  const [q, setQ] = useState(params.get("q") ?? "");
  useEffect(() => setQ(params.get("q") ?? ""), [params]);

  const remote = params.get("remote");
  const employmentType = params.get("employment_type");
  const sponsors = params.get("sponsors_visa");
  const skill = params.get("q"); // skill chips piggyback on the q param for now
  const location = params.get("location") ?? "";

  const activeCount = useMemo(() => {
    let n = 0;
    if (remote !== null) n++;
    if (employmentType) n++;
    if (sponsors === "true") n++;
    if (location) n++;
    if (params.get("q")) n++;
    return n;
  }, [remote, employmentType, sponsors, location, params]);

  function commit(update: Record<string, FilterValue>) {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(update)) {
      if (value === null || value === "") next.delete(key);
      else next.set(key, String(value));
    }
    next.delete("offset");
    startTransition(() => router.push(`/?${next.toString()}`));
  }

  return (
    <section
      aria-label="Filter jobs"
      className={cn(
        "rounded-xl border border-border/70 bg-card/80 p-3 shadow-card backdrop-blur sm:p-4",
        pending && "opacity-90",
      )}
    >
      <form
        className="flex flex-col gap-3 sm:flex-row sm:items-stretch"
        onSubmit={(e) => {
          e.preventDefault();
          commit({ q: q.trim() || null });
        }}
        role="search"
      >
        <div className="relative flex-1">
          <SearchIcon />
          <Input
            placeholder="Search by role, company, or skill"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="pl-9"
            aria-label="Search jobs"
          />
        </div>
        <div className="flex gap-2">
          <Input
            placeholder="Location"
            defaultValue={location}
            onBlur={(e) => commit({ location: e.target.value.trim() || null })}
            className="sm:max-w-[180px]"
            aria-label="Location"
          />
          <Button type="submit" className="shrink-0">
            Search
          </Button>
        </div>
      </form>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
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
            onClick={() => startTransition(() => router.push("/"))}
            className="ml-auto inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            Clear all
            <Badge variant="muted">{activeCount}</Badge>
          </button>
        )}
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
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-2.5 py-1 text-[11px] font-medium leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
        active
          ? "border-primary/40 bg-primary/10 text-primary"
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
