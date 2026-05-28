"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Search, SlidersHorizontal } from "lucide-react";

import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

const JOB_TYPES = [
  { value: "", label: "Any job type" },
  { value: "full-time", label: "Full-time" },
  { value: "part-time", label: "Part-time" },
  { value: "contract", label: "Contract" },
  { value: "internship", label: "Internship" },
];

const POSTED_WITHIN = [
  { value: "", label: "Any time" },
  { value: "24h", label: "Past 24h" },
  { value: "7d", label: "Past 7 days" },
  { value: "30d", label: "Past 30 days" },
];

const SEARCH_DEBOUNCE_MS = 250;

/**
 * Sticky filter bar above the split panes. All filter state lives in the
 * URL query (shareable + restored on refresh). Text inputs are debounced
 * (~250ms); selects/toggles apply immediately. On <1024px the full set
 * collapses behind a "Filters" sheet, leaving just the search box.
 */
export function JobsFilterBar({ total }: { total: number | null }) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  // Write a param (empty/false clears it) onto the CURRENT path so a
  // selected job stays selected while filters change.
  const setParam = useCallback(
    (updates: Record<string, string | boolean | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [key, value] of Object.entries(updates)) {
        if (value === "" || value === false || value == null) next.delete(key);
        else next.set(key, String(value));
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, pathname, router],
  );

  return (
    <div className="border-b border-border/60 bg-background/90 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="flex items-center gap-2 px-4 py-3 sm:px-6">
        <DebouncedInput
          value={params.get("q") ?? ""}
          onCommit={(v) => setParam({ q: v })}
          placeholder="Search title, company, keyword"
          icon
          className="min-w-0 flex-1"
        />

        {/* Desktop: full filter set inline. */}
        <div className="hidden items-center gap-2 lg:flex">
          <DebouncedInput
            value={params.get("location") ?? ""}
            onCommit={(v) => setParam({ location: v })}
            placeholder="Location"
            className="w-40"
          />
          <FilterControls params={params} setParam={setParam} />
        </div>

        {/* Mobile: a Filters button opening a sheet with the full set. */}
        <div className="lg:hidden">
          <Sheet>
            <SheetTrigger
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-3 py-2 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="Open filters"
            >
              <SlidersHorizontal className="h-4 w-4" aria-hidden />
              Filters
            </SheetTrigger>
            <SheetContent title="Filters">
              <div className="space-y-4">
                <DebouncedInput
                  value={params.get("location") ?? ""}
                  onCommit={(v) => setParam({ location: v })}
                  placeholder="Location"
                  className="w-full"
                />
                <FilterControls params={params} setParam={setParam} stacked />
              </div>
            </SheetContent>
          </Sheet>
        </div>
      </div>

      {/* Result count + keyboard hint — subtle, desktop only. */}
      <div className="hidden items-center justify-between px-6 pb-2 text-xs text-muted-foreground lg:flex">
        <span aria-live="polite">
          {total === null ? "Loading jobs…" : `${total} ${total === 1 ? "job" : "jobs"}`}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <kbd className="rounded border border-border bg-muted px-1 font-sans text-[10px]">↑↓</kbd>
          to navigate
        </span>
      </div>
    </div>
  );
}

function FilterControls({
  params,
  setParam,
  stacked = false,
}: {
  params: URLSearchParams;
  setParam: (u: Record<string, string | boolean | null>) => void;
  stacked?: boolean;
}) {
  return (
    <div className={cn("flex gap-2", stacked ? "flex-col" : "items-center")}>
      <SelectControl
        label="Job type"
        value={params.get("job_type") ?? ""}
        options={JOB_TYPES}
        onChange={(v) => setParam({ job_type: v })}
        className={stacked ? "w-full" : undefined}
      />
      <SelectControl
        label="Posted within"
        value={params.get("posted_within") ?? ""}
        options={POSTED_WITHIN}
        onChange={(v) => setParam({ posted_within: v })}
        className={stacked ? "w-full" : undefined}
      />

      {/* Sponsorship-status filter — visible but disabled until LCA data ships. */}
      <span
        title="Coming soon"
        aria-disabled="true"
        className={cn(
          "inline-flex cursor-not-allowed items-center gap-1.5 rounded-md border border-dashed border-border px-3 py-2 text-sm font-medium text-muted-foreground/60",
          stacked && "w-full justify-center",
        )}
      >
        Sponsorship
        <span className="text-[10px] uppercase tracking-wide">soon</span>
      </span>
    </div>
  );
}

function SelectControl({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (v: string) => void;
  className?: string;
}) {
  return (
    <label className={cn("relative", className)}>
      <span className="sr-only">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-10 w-full appearance-none rounded-md border border-border bg-card pl-3 pr-8 text-sm font-medium text-foreground transition-colors hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
      >
        ▾
      </span>
    </label>
  );
}

/** Text input that commits to the URL after a debounce. Stays controlled
 * locally so typing is smooth; syncs down if the URL changes externally
 * (e.g. back button). */
function DebouncedInput({
  value,
  onCommit,
  placeholder,
  className,
  icon = false,
}: {
  value: string;
  onCommit: (v: string) => void;
  placeholder: string;
  className?: string;
  icon?: boolean;
}) {
  const [local, setLocal] = useState(value);
  const committed = useRef(value);

  // Sync down when the URL value changes from outside this input.
  useEffect(() => {
    if (value !== committed.current) {
      committed.current = value;
      setLocal(value);
    }
  }, [value]);

  useEffect(() => {
    if (local === committed.current) return;
    const t = setTimeout(() => {
      committed.current = local;
      onCommit(local);
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [local, onCommit]);

  return (
    <div className={cn("relative", className)}>
      {icon && (
        <Search
          className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden
        />
      )}
      <Input
        value={local}
        onChange={(e) => setLocal(e.target.value)}
        placeholder={placeholder}
        className={cn("h-10", icon && "pl-9")}
        aria-label={placeholder}
      />
    </div>
  );
}
