"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";

type FilterValue = string | boolean | null;

export function JobFilters() {
  const router = useRouter();
  const params = useSearchParams();

  const [q, setQ] = useState(params.get("q") ?? "");

  function commit(update: Record<string, FilterValue>) {
    const next = new URLSearchParams(params.toString());
    for (const [key, value] of Object.entries(update)) {
      if (value === null || value === "") {
        next.delete(key);
      } else {
        next.set(key, String(value));
      }
    }
    next.delete("offset"); // any filter change resets pagination
    router.push(`/?${next.toString()}`);
  }

  const remote = params.get("remote");
  const sponsors = params.get("sponsors_visa");

  return (
    <div className="space-y-3">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          commit({ q: q.trim() || null });
        }}
      >
        <Input
          placeholder="Search title or company…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
      </form>

      <div className="flex flex-wrap gap-2">
        <FilterChip
          label="Remote"
          active={remote === "true"}
          onClick={() => commit({ remote: remote === "true" ? null : true })}
        />
        <FilterChip
          label="On-site"
          active={remote === "false"}
          onClick={() => commit({ remote: remote === "false" ? null : false })}
        />
        <FilterChip
          label="Sponsors visa"
          active={sponsors === "true"}
          onClick={() => commit({ sponsors_visa: sponsors === "true" ? null : true })}
        />
        {(remote !== null || sponsors !== null || params.get("q")) && (
          <button
            type="button"
            onClick={() => router.push("/")}
            className="text-xs text-muted-foreground underline underline-offset-4"
          >
            Clear all
          </button>
        )}
      </div>
    </div>
  );
}

function FilterChip({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button type="button" onClick={onClick}>
      <Badge variant={active ? "default" : "outline"} className="cursor-pointer">
        {label}
      </Badge>
    </button>
  );
}
