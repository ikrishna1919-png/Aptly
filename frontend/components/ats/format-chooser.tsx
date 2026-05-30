"use client";

import { useEffect, useState } from "react";
import { Sparkles, Upload, LayoutGrid } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  aiChooseFormat,
  getDefaultFormat,
  parseAtsUpload,
  setDefaultFormat,
} from "@/lib/ats";

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

  useEffect(() => {
    getDefaultFormat(kind)
      .then((d) => setCurrent(d.format))
      .catch(() => setCurrent(null));
  }, [kind]);

  async function pick(format: string) {
    setBusy(true);
    setNote(null);
    try {
      await setDefaultFormat(kind, format);
      setCurrent(format);
      setNote(`Saved — ${format} is now your default.`);
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

  async function onUpload(file: File) {
    setBusy(true);
    setNote(null);
    try {
      const res = await parseAtsUpload(file);
      if (res.kind === "docx") {
        setNote(
          "We'll preserve your uploaded DOCX formatting for future generations (in-place keyword injection).",
        );
      } else {
        setNote(
          "PDF can't be preserved exactly — we'll pick the closest matching pre-built format when you generate.",
        );
      }
    } catch (e) {
      setNote(e instanceof Error ? e.message : "Upload failed");
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

      {/* 1b match upload */}
      <Section icon={Upload} title="Match an uploaded resume">
        <p className="text-sm text-muted-foreground">
          Upload a DOCX to preserve its exact formatting, or a PDF and we&apos;ll find the closest
          match. (DOCX preserves formatting reliably; PDF can&apos;t.)
        </p>
        <label className="mt-3 inline-flex cursor-pointer items-center gap-2 rounded-md border border-border px-4 py-2 text-sm font-medium hover:border-primary/40">
          <Upload className="h-4 w-4" aria-hidden /> Upload DOCX or PDF
          <input
            type="file"
            accept=".docx,.pdf"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && onUpload(e.target.files[0])}
          />
        </label>
      </Section>

      {/* 1c pick a pre-built */}
      <Section icon={LayoutGrid} title="Pick a format">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {formats.map((f) => (
            <button
              key={f.id}
              disabled={busy}
              onClick={() => void pick(f.id)}
              className={cn(
                "rounded-xl border p-3 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                current === f.id ? "border-primary ring-1 ring-primary" : "border-border hover:border-primary/40",
              )}
            >
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
