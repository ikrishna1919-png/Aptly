"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useAuthGate } from "@/lib/use-login-modal";
import {
  type CoverLetter,
  coverLetterDownloadUrl,
  generateCoverLetter,
  updateCoverLetter,
} from "@/lib/ats";

type Step = "input" | "result";

export default function CoverLetterPage() {
  const gate = useAuthGate();
  const [step, setStep] = useState<Step>("input");
  const [jd, setJd] = useState("");
  const [company, setCompany] = useState("");
  const [hook, setHook] = useState("");
  const [tone, setTone] = useState("confident");
  const [length, setLength] = useState("standard");
  const [opening, setOpening] = useState("value");
  const [additional, setAdditional] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [letter, setLetter] = useState<CoverLetter | null>(null);

  async function generate() {
    if (!gate("tailor")) return;
    setBusy(true);
    setError(null);
    try {
      const res = await generateCoverLetter({
        jd_text: jd,
        company_name: company,
        hook,
        questions: { tone, length, opening, additional },
      });
      setLetter(res);
      setStep("result");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
    } finally {
      setBusy(false);
    }
  }

  if (step === "result" && letter?.content) {
    return <CoverResult letter={letter} onChange={setLetter} onBack={() => setStep("input")} />;
  }

  return (
    <div className="container max-w-2xl py-10">
      <h1 className="font-display text-2xl font-bold tracking-tight">Cover letter generator</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Grounded in your real profile — we never invent achievements or experience.
      </p>

      <label className="mt-6 block text-sm font-medium">Job description</label>
      <Textarea
        value={jd}
        onChange={(e) => setJd(e.target.value)}
        rows={8}
        placeholder="Paste the job description…"
        className="mt-1 text-sm"
      />
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div>
          <label className="block text-sm font-medium">Company (optional)</label>
          <Input value={company} onChange={(e) => setCompany(e.target.value)} className="mt-1" />
        </div>
        <div>
          <label className="block text-sm font-medium">Hook (optional)</label>
          <Input
            value={hook}
            onChange={(e) => setHook(e.target.value)}
            placeholder="e.g. I met your CTO at SXSW"
            className="mt-1"
          />
        </div>
      </div>

      <div className="mt-6 space-y-4">
        <Q label="Tone">
          <Seg options={[["formal", "Formal"], ["confident", "Confident"], ["warm", "Warm"]]} value={tone} onChange={setTone} />
        </Q>
        <Q label="Length">
          <Seg options={[["short", "Short ~200"], ["standard", "Standard ~350"], ["long", "Long ~500"]]} value={length} onChange={setLength} />
        </Q>
        <Q label="Opening style">
          <Seg options={[["value", "Direct value"], ["story", "Story-driven"], ["question", "Question hook"]]} value={opening} onChange={setOpening} />
        </Q>
        <Q label="Anything else?">
          <Textarea value={additional} onChange={(e) => setAdditional(e.target.value)} rows={2} className="text-sm" />
        </Q>
      </div>

      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
      <Button className="mt-6" disabled={busy || jd.trim().length < 50} onClick={() => void generate()}>
        {busy ? "Generating…" : "Generate cover letter"}
      </Button>
    </div>
  );
}

function CoverResult({
  letter,
  onChange,
  onBack,
}: {
  letter: CoverLetter;
  onChange: (l: CoverLetter) => void;
  onBack: () => void;
}) {
  const c = letter.content!;
  const [saving, setSaving] = useState(false);

  function setPara(i: number, value: string) {
    const paragraphs = c.paragraphs.slice();
    paragraphs[i] = value;
    onChange({ ...letter, content: { ...c, paragraphs } });
  }

  async function save() {
    setSaving(true);
    try {
      await updateCoverLetter(letter.id, letter.content);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="container max-w-2xl py-10">
      <button onClick={onBack} className="mb-3 text-sm text-muted-foreground hover:text-foreground">
        ← Start over
      </button>
      <h1 className="font-display text-2xl font-bold tracking-tight">Your cover letter</h1>
      {letter.demo_mode && (
        <Badge variant="muted" className="mt-2">
          demo mode — set ANTHROPIC_API_KEY for live generation
        </Badge>
      )}

      <div className="mt-5 space-y-3 rounded-xl border border-border bg-card p-5">
        <p className="text-sm text-muted-foreground">{c.recipient}</p>
        <p className="text-sm">{c.greeting}</p>
        {c.paragraphs.map((p, i) => (
          <Textarea key={i} value={p} onChange={(e) => setPara(i, e.target.value)} rows={3} className="text-sm" />
        ))}
        <p className="text-sm">{c.closing}</p>
        <p className="text-sm font-medium">{c.signature}</p>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
        <Button variant="ghost" size="sm" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save edits"}
        </Button>
        <Button asChild>
          <a href={coverLetterDownloadUrl(letter.id, "docx")} onClick={() => void save()}>
            Download DOCX
          </a>
        </Button>
        <Button asChild variant="outline">
          <a href={coverLetterDownloadUrl(letter.id, "pdf")} onClick={() => void save()}>
            Download PDF
          </a>
        </Button>
        <span className="text-xs text-muted-foreground">Format: {letter.format}</span>
      </div>
    </div>
  );
}

function Q({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-sm font-medium">{label}</p>
      <div className="mt-1">{children}</div>
    </div>
  );
}

function Seg({
  options,
  value,
  onChange,
}: {
  options: [string, string][];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div role="radiogroup" className="inline-flex flex-wrap gap-1 rounded-md border border-border bg-card p-0.5">
      {options.map(([v, label]) => (
        <button
          key={v}
          role="radio"
          aria-checked={value === v}
          onClick={() => onChange(v)}
          className={cn(
            "rounded px-3 py-1 text-xs font-medium transition-colors",
            value === v ? "bg-primary text-primary-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
