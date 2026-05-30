"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Sparkles, Upload, LayoutGrid, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getActiveResume } from "@/lib/api";
import { aiChooseFormat, getDefaultFormat, setDefaultFormat } from "@/lib/ats";

/**
 * Shared "choose default format" UI for Feature #1 (resume) and #5 (cover).
 * Three sub-options: AI chooses (heuristic), match an upload, or pick from the
 * pre-builts. `formats` + `kind` differ per feature; everything else is shared.
 */
export function FormatChooser({
  kind,
  title,
  formats,
}: {
  kind: "resume" | "cover";
  title: string;
  formats: { id: string; name: string; blurb: string }[];
}) {
  const [current, setCurrent] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // "Match uploaded" is only valid when a DOCX resume is saved on the profile
  // (formatting can only be preserved for DOCX). null = still loading.
  const [hasDocxResume, setHasDocxResume] = useState<boolean | null>(null);

  useEffect(() => {
    getDefaultFormat(kind)
      .then((d) => setCurrent(d.format))
      .catch(() => setCurrent(null));
  }, [kind]);

  useEffect(() => {
    if (kind !== "resume") {
      setHasDocxResume(false);
      return;
    }
    getActiveResume()
      .then((r) =>
        setHasDocxResume(
          r.present &&
            r.content_type ===
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
      )
      .catch(() => setHasDocxResume(false));
  }, [kind]);

  // Auto-fade the "Saved as default" toast.
  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), 2500);
    return () => clearTimeout(t);
  }, [note]);

  async function pick(format: string) {
    setBusy(true);
    setNote(null);
    try {
      await setDefaultFormat(kind, format);
      setCurrent(format);
      setNote("Saved as default.");
    } finally {
      setBusy(false);
    }
  }

  async function aiChoose() {
    setBusy(true);
    setNote(null);
    try {
      const d = await aiChooseFormat(kind);
      setCurrent(d.format);
      setNote(`AI picked ${d.format}${d.reason ? ` — ${d.reason}` : ""}. Saved as default.`);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container max-w-3xl py-10">
      <h1 className="font-display text-2xl font-bold tracking-tight">{title}</h1>
      {current && (
        <p className="mt-2 text-sm text-muted-foreground">
          Current default: <span className="font-medium text-foreground">{current}</span>
        </p>
      )}

      {/* 1a AI chooses */}
      <Section icon={Sparkles} title="Let AI choose">
        <p className="text-sm text-muted-foreground">
          We pick the most fitting pre-built based on your experience level and field (a simple,
          explainable rule — not a black box).
        </p>
        <Button className="mt-3" disabled={busy} onClick={() => void aiChoose()}>
          Choose for me
        </Button>
      </Section>

      {/* 1b match uploaded resume — enabled only with a saved DOCX resume */}
      {kind === "resume" && (
        <Section icon={Upload} title="Match your uploaded resume">
          <p className="text-sm text-muted-foreground">
            Reuse the exact formatting of the DOCX resume saved on your profile.
          </p>
          {hasDocxResume ? (
            <Button
              className="mt-3"
              variant="outline"
              disabled={busy}
              onClick={() => void pick("match_upload")}
            >
              Use my uploaded resume&apos;s format
            </Button>
          ) : (
            <Link
              href="/profile"
              title="Upload a DOCX resume to your profile first."
              className="mt-3 inline-flex cursor-not-allowed items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium text-muted-foreground/70"
              aria-disabled="true"
            >
              <Upload className="h-4 w-4" aria-hidden />
              Upload a DOCX resume to your profile first
            </Link>
          )}
        </Section>
      )}

      {/* 1c pick a pre-built */}
      <Section icon={LayoutGrid} title="Pick a format">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {formats.map((f) => (
            <button
              key={f.id}
              disabled={busy}
              onClick={() => void pick(f.id)}
              className={cn(
                "relative rounded-xl border p-3 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                current === f.id
                  ? "border-2 border-primary bg-primary-soft/40"
                  : "border-border hover:border-primary/40",
              )}
            >
              {current === f.id && (
                <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground">
                  <Check className="h-3 w-3" aria-hidden /> Current default
                </span>
              )}
              <div className="flex h-16 flex-col gap-1 rounded-md border border-border bg-background p-2" aria-hidden>
                <div className={cn("h-2 w-2/3 rounded", f.id === "modern" ? "bg-primary" : "bg-foreground/70")} />
                <div className="h-1 w-1/3 rounded bg-muted-foreground/40" />
                <div className="mt-1 h-px w-full bg-border" />
                {[0, 1].map((i) => (
                  <div key={i} className="h-1 rounded bg-muted-foreground/25" style={{ width: `${85 - i * 20}%` }} />
                ))}
              </div>
              <p className="mt-2 text-sm font-semibold">{f.name}</p>
              <p className="text-xs text-muted-foreground">{f.blurb}</p>
            </button>
          ))}
        </div>
      </Section>

      {note && (
        <p className="mt-4 rounded-md border border-primary/15 bg-primary-soft/40 px-3 py-2 text-sm text-foreground/90">
          {note}
        </p>
      )}
    </div>
  );
}

function Section({
  icon: Icon,
  title,
  children,
}: {
  icon: typeof Sparkles;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-6 rounded-xl border border-border p-5">
      <div className="flex items-center gap-2">
        <Icon className="h-5 w-5 text-primary" aria-hidden />
        <h2 className="text-base font-semibold">{title}</h2>
      </div>
      <div className="mt-2">{children}</div>
    </section>
  );
}
