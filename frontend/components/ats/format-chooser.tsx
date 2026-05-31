"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Sparkles, Upload, LayoutGrid, Check } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getActiveResume } from "@/lib/api";
import {
  aiChooseFormat,
  getDefaultFormat,
  type ResumeSource,
  setDefaultFormat,
} from "@/lib/ats";

/**
 * "Choose default format" UI. For RESUME (Feature #1) this is a single-select
 * of the tailoring SOURCE: (a) Let AI choose, (b) Match my resume format
 * (in-place keyword edits on the saved DOCX), (c) Choose available (coming
 * soon). The choice is saved on default_resume_format and routes BOTH the ATS
 * generator and the Jobs tailor flow. For COVER (#5) the original
 * AI/pick-a-prebuilt UI is kept (no docx-inject path for cover letters).
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
  const [source, setSource] = useState<ResumeSource | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  // "Match my resume format" is only valid when a DOCX resume is saved on the
  // profile (we can only edit a .docx in place). null = still loading.
  const [hasDocxResume, setHasDocxResume] = useState<boolean | null>(null);

  useEffect(() => {
    getDefaultFormat(kind)
      .then((d) => {
        setCurrent(d.format);
        if (kind === "resume") setSource((d.source as ResumeSource) ?? "ai");
      })
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

  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), 3000);
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

  // ── RESUME: a/b/c source single-select ─────────────────────────────────────
  if (kind === "resume") {
    const selected: ResumeSource = source ?? "ai";
    const locked = !editing;

    async function chooseSource(next: ResumeSource) {
      if (busy) return;
      if (next === "available") {
        setNote("A template library is coming soon — for now use AI-choose or match your resume.");
        return;
      }
      if (next === "resume" && !hasDocxResume) {
        setNote("Upload a .docx resume on your Profile first to match its format.");
        return;
      }
      setBusy(true);
      setNote(null);
      try {
        if (next === "ai") {
          // Heuristic format pick, then stamp the source so routing uses generate.
          const d = await aiChooseFormat("resume");
          await setDefaultFormat("resume", d.format, null, "ai");
          setCurrent(d.format);
          setNote(`Let AI choose — picked ${d.format}${d.reason ? ` (${d.reason})` : ""}.`);
        } else {
          await setDefaultFormat("resume", current ?? "modern", null, "resume");
          setNote("We'll tailor by editing your saved resume in place — formatting preserved.");
        }
        setSource(next);
        setEditing(false);
      } finally {
        setBusy(false);
      }
    }

    const options: {
      id: ResumeSource;
      icon: typeof Sparkles;
      name: string;
      blurb: string;
      comingSoon?: boolean;
    }[] = [
      {
        id: "ai",
        icon: Sparkles,
        name: "Let AI choose",
        blurb:
          "We generate a fresh, ATS-friendly resume and pick the most fitting layout for your background.",
      },
      {
        id: "resume",
        icon: Upload,
        name: "Match my resume format",
        blurb:
          "Tailor by editing the existing text of your saved .docx in place — your exact formatting is preserved. (Rewrites wording only; never adds new sections or lines.)",
      },
      {
        id: "available",
        icon: LayoutGrid,
        name: "Choose available",
        blurb: "Pick from a library of templates.",
        comingSoon: true,
      },
    ];

    return (
      <div className="container max-w-3xl py-10">
        <h1 className="font-display text-2xl font-bold tracking-tight">{title}</h1>
        <p className="mt-2 text-sm text-muted-foreground">
          How should tailoring build your resume? This applies to the ATS generator and the
          one-click &ldquo;Tailor for this job&rdquo; on every posting.
        </p>

        <div className="mt-6 grid gap-3 sm:grid-cols-3">
          {options.map((o) => {
            const isSelected = selected === o.id;
            const greyed = (locked && !isSelected) || o.comingSoon;
            return (
              <button
                key={o.id}
                type="button"
                disabled={busy || (locked && !isSelected) || o.comingSoon}
                onClick={() => void chooseSource(o.id)}
                aria-pressed={isSelected}
                className={cn(
                  "relative rounded-xl border p-4 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isSelected
                    ? "border-2 border-primary bg-primary-soft/40"
                    : "border-border hover:border-primary/40",
                  greyed && "opacity-50",
                )}
              >
                {isSelected && (
                  <span className="absolute right-2 top-2 inline-flex items-center gap-1 rounded-full bg-primary px-2 py-0.5 text-[10px] font-semibold text-primary-foreground">
                    <Check className="h-3 w-3" aria-hidden /> Selected
                  </span>
                )}
                {o.comingSoon && (
                  <span className="absolute right-2 top-2 rounded-full border border-border px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
                    Coming soon
                  </span>
                )}
                <o.icon className="h-5 w-5 text-primary" aria-hidden />
                <p className="mt-2 text-sm font-semibold">{o.name}</p>
                <p className="mt-1 text-xs text-muted-foreground">{o.blurb}</p>
                {o.id === "resume" && hasDocxResume === false && (
                  <Link
                    href="/profile"
                    className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-primary underline"
                  >
                    <Upload className="h-3 w-3" aria-hidden /> Upload a .docx on your Profile first
                  </Link>
                )}
              </button>
            );
          })}
        </div>

        {locked && (
          <Button
            className="mt-4"
            variant="outline"
            size="sm"
            disabled={busy}
            onClick={() => setEditing(true)}
          >
            Edit
          </Button>
        )}

        {note && (
          <p className="mt-4 rounded-md border border-primary/15 bg-primary-soft/40 px-3 py-2 text-sm text-foreground/90">
            {note}
          </p>
        )}
      </div>
    );
  }

  // ── COVER: original AI / pick-a-prebuilt UI (unchanged) ─────────────────────
  return (
    <div className="container max-w-3xl py-10">
      <h1 className="font-display text-2xl font-bold tracking-tight">{title}</h1>
      {current && (
        <p className="mt-2 text-sm text-muted-foreground">
          Current default: <span className="font-medium text-foreground">{current}</span>
        </p>
      )}

      <Section icon={Sparkles} title="Let AI choose">
        <p className="text-sm text-muted-foreground">
          We pick the most fitting pre-built based on your experience level and field (a simple,
          explainable rule — not a black box).
        </p>
        <Button className="mt-3" disabled={busy} onClick={() => void aiChoose()}>
          Choose for me
        </Button>
      </Section>

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
