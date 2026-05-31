"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { FileText, Upload, ArrowLeft, Check } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { useAuthGate } from "@/lib/use-login-modal";
import {
  fetchJob,
  fetchProfile,
  downloadResume,
  getActiveResume,
  type TailoredResume,
} from "@/lib/api";
import {
  ATS_MAX_WAIT_MS,
  ATS_POLL_MS,
  type AtsCustomOptions,
  type AtsFormat,
  type AtsQuestions,
  type AtsRun,
  downloadAtsDocx,
  fetchAtsRun,
  getDefaultFormat,
  keywordCoverage,
  parseAtsUpload,
  startAtsGenerate,
} from "@/lib/ats";

type Step = "chooser" | "entry" | "questions" | "format" | "preview";
type Option = "jd" | "upload";
type UploadKind = "docx" | "pdf";

const EASE = [0.22, 1, 0.36, 1] as const;
const FORMATS: { id: AtsFormat; name: string; blurb: string }[] = [
  { id: "modern", name: "Modern", blurb: "Clean sans, subtle blue accents" },
  { id: "classic", name: "Classic", blurb: "Traditional serif, two-line blocks" },
  { id: "minimal", name: "Minimal", blurb: "Generous whitespace, light weight" },
  { id: "plain", name: "Plain", blurb: "Max ATS compat, no styling" },
];
const ACCENTS = ["blue", "slate", "teal", "plum", "none"];

const DEFAULT_ANSWERS: AtsQuestions = {
  length: "1",
  tone: "confident",
  emphasis: "mixed",
  skills: [],
  roles: [],
  additional: "",
};

export function AtsHub() {
  const reduce = useReducedMotion();
  const gate = useAuthGate();
  const router = useRouter();
  const params = useSearchParams();

  const [step, setStep] = useState<Step>("chooser");
  const [option, setOption] = useState<Option | null>(null);
  const [jdText, setJdText] = useState("");
  const [uploadKind, setUploadKind] = useState<UploadKind | null>(null);
  const [uploadId, setUploadId] = useState<string | null>(null);
  const [uploadName, setUploadName] = useState<string | null>(null);
  const [pdfNotice, setPdfNotice] = useState(false);
  const [answers, setAnswers] = useState<AtsQuestions>(DEFAULT_ANSWERS);
  const [format, setFormat] = useState<AtsFormat>("modern");
  // FIX 6: the user's saved default format label (for display) + a per-
  // generation override picked on the questions step. `null` override = use
  // the saved default.
  const [defaultFmtLabel, setDefaultFmtLabel] = useState<string>("Modern");
  const [overrideFmt, setOverrideFmt] = useState<AtsFormat | null>(null);
  // Saved tailoring SOURCE ("ai" | "resume" | "available") + whether a DOCX is
  // saved. When source is "resume" (match my resume format) we SKIP the
  // customize screen and go straight to the in-place docx_inject run.
  const [source, setSource] = useState<string | null>(null);
  const [hasDocx, setHasDocx] = useState<boolean>(false);
  const [custom, setCustom] = useState<AtsCustomOptions>({
    base: "modern",
    accent_color: "blue",
    font_family: "sans",
    margins: "normal",
  });
  const [profileSkills, setProfileSkills] = useState<string[]>([]);
  const [profileRoles, setProfileRoles] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<AtsRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const startedAt = useRef(0);

  // Pre-fill from a job deep-link (?jobId=) → jump straight to questions.
  useEffect(() => {
    const jobId = params.get("jobId");
    if (!jobId) return;
    let cancelled = false;
    (async () => {
      try {
        const job = await fetchJob(Number(jobId));
        if (!cancelled && job?.description) {
          setOption("jd");
          setJdText(job.description);
          // Land on the JD step; "Continue" then routes by saved source
          // (customize for A, straight-to-docx_inject for B).
          setStep("entry");
        }
      } catch {
        /* fall back to chooser */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [params]);

  // FIX 6: load the user's saved default resume format on mount, so the
  // format-selection step can be skipped. Falls back to Modern (the backend
  // returns "modern" when unset). Maps the stored value onto the AtsFormat
  // union; anything outside it (e.g. "match_upload") falls back to Modern for
  // the generated-resume path.
  useEffect(() => {
    const KNOWN: AtsFormat[] = ["modern", "classic", "minimal", "plain", "custom"];
    getDefaultFormat("resume")
      .then((d) => {
        const f = d.format as AtsFormat;
        setFormat(KNOWN.includes(f) ? f : "modern");
        setDefaultFmtLabel(d.format.charAt(0).toUpperCase() + d.format.slice(1));
        setSource((d.source as string) ?? "ai");
      })
      .catch(() => {
        /* keep Modern fallback */
      });
    // Whether a DOCX is saved (needed for the "match my resume format" route).
    getActiveResume()
      .then((r) =>
        setHasDocx(
          !!r.present &&
            r.content_type ===
              "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ),
      )
      .catch(() => setHasDocx(false));
  }, []);

  // Load profile skills/roles for the chip pickers (best-effort).
  useEffect(() => {
    (async () => {
      try {
        const p = await fetchProfile();
        const skills = (p.skills ?? []).flatMap((s: unknown) =>
          typeof s === "string" ? [s] : ((s as { items?: string[] }).items ?? []),
        );
        setProfileSkills([...new Set(skills)].slice(0, 24));
        setProfileRoles(
          (p.experience ?? [])
            .map((e) => [e.title, e.company].filter(Boolean).join(" · "))
            .filter(Boolean)
            .slice(0, 10),
        );
      } catch {
        /* chips just won't populate; AI picks by default */
      }
    })();
  }, []);

  // Poll the run while generating.
  useEffect(() => {
    if (!runId || step !== "preview") return;
    if (run && (run.status === "done" || run.status === "error")) return;
    if (run && Date.now() - startedAt.current > ATS_MAX_WAIT_MS) {
      setError("This is taking longer than expected. Try again.");
      return;
    }
    let cancelled = false;
    const t = setTimeout(
      async () => {
        try {
          const next = await fetchAtsRun(runId);
          if (!cancelled) setRun(next);
        } catch {
          if (!cancelled) setRun((p) => (p ? { ...p } : p));
        }
      },
      run === null ? 0 : ATS_POLL_MS,
    );
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [runId, run, step]);

  const optionType = useMemo(() => {
    if (option === "jd") return "jd_paste" as const;
    if (uploadKind === "pdf") return "upload_pdf_fallback" as const;
    return "upload_docx" as const;
  }, [option, uploadKind]);

  const isDocxPath = option === "upload" && uploadKind === "docx";

  async function onUpload(file: File) {
    if (!gate("tailor")) return;
    if (file.size > 5 * 1024 * 1024) {
      setError("File too large (max 5 MB).");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await parseAtsUpload(file);
      setUploadName(file.name);
      if (res.kind === "docx") {
        setUploadKind("docx");
        setUploadId(res.upload_id);
      } else {
        setUploadKind("pdf");
        setPdfNotice(true);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onGenerate() {
    if (!gate("tailor")) return;
    setBusy(true);
    setError(null);
    // Override (this generation only) wins over the saved default.
    const effectiveFmt: AtsFormat = overrideFmt ?? format;
    try {
      const { run_id } = await startAtsGenerate({
        option_type: optionType,
        jd_text: jdText,
        questions: answers,
        format: isDocxPath ? "plain" : effectiveFmt,
        custom_options: effectiveFmt === "custom" ? custom : null,
        upload_id: uploadId,
      });
      startedAt.current = Date.now();
      setRunId(run_id);
      setRun(null);
      setStep("preview");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't start generation");
    } finally {
      setBusy(false);
    }
  }

  // After the JD is submitted, both paths show the SAME 6-question customize
  // screen. For option B the answers STEER which existing keywords/skills to
  // weave in (they don't add content); generate runs the in-place docx_inject.
  // Guard: option B needs a saved DOCX first.
  function continueFromJd() {
    if (source === "resume" && !hasDocx) {
      setError(
        "To use “Match my resume format”, upload a .docx resume on your Profile first — " +
          "or switch your ATS format to “Let AI choose”.",
      );
      return;
    }
    setStep("questions");
  }

  function reset() {
    setStep("chooser");
    setOption(null);
    setJdText("");
    setUploadKind(null);
    setUploadId(null);
    setPdfNotice(false);
    setAnswers(DEFAULT_ANSWERS);
    setRunId(null);
    setRun(null);
    setError(null);
    router.replace("/ats");
  }

  const anim = reduce
    ? {}
    : { initial: { opacity: 0, y: 12 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -12 } };

  return (
    <div className="container max-w-5xl py-10">
      <AnimatePresence mode="wait">
        <motion.div key={step} {...anim} transition={{ duration: 0.3, ease: EASE }}>
          {step === "chooser" && (
            <Chooser
              onJd={() => {
                setOption("jd");
                setStep("entry");
              }}
            />
          )}

          {step === "entry" && option === "jd" && (
            <JdEntry value={jdText} onChange={setJdText} onBack={reset} onContinue={continueFromJd} />
          )}

          {step === "entry" && option === "upload" && (
            <UploadEntry
              busy={busy}
              uploadKind={uploadKind}
              uploadName={uploadName}
              pdfNotice={pdfNotice}
              jdText={jdText}
              onJdChange={setJdText}
              onPick={onUpload}
              onConfirmPdf={() => setPdfNotice(false)}
              onBack={reset}
              onContinue={() => setStep("questions")}
            />
          )}

          {step === "questions" && (
            <Questions
              answers={answers}
              onChange={setAnswers}
              skills={profileSkills}
              roles={profileRoles}
              onBack={() => setStep("entry")}
              // FIX 6: format selection is skipped — the saved default applies.
              // For the keyword-inject (DOCX-upload) path the format is the
              // uploaded doc, so go straight to generate either way.
              onContinue={() => void onGenerate()}
              busy={busy}
              defaultFormat={defaultFmtLabel}
              overrideFormat={overrideFmt}
              onOverrideFormat={setOverrideFmt}
              // Option B (match my resume format) preserves the saved DOCX's
              // format, so show the in-place note + hide the format-override.
              showDocxNote={isDocxPath || source === "resume"}
            />
          )}

          {step === "format" && (
            <FormatStep
              isDocxPath={isDocxPath}
              format={format}
              setFormat={setFormat}
              custom={custom}
              setCustom={setCustom}
              busy={busy}
              onBack={() => setStep("questions")}
              onGenerate={onGenerate}
              onNewVersion={() => {
                // "Generate a new version" from the original-format note:
                // switch to a fresh Option-1 render path.
                setUploadKind(null);
                setOption("jd");
              }}
            />
          )}

          {step === "preview" && (
            <Preview run={run} format={format} custom={custom} runId={runId} onStartOver={reset} />
          )}
        </motion.div>
      </AnimatePresence>

      {error && (
        <p role="alert" className="mt-4 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

// ─── Step 1: chooser ──────────────────────────────────────────────────────────

function Chooser({ onJd }: { onJd: () => void }) {
  return (
    <div>
      <h1 className="font-display text-3xl font-bold tracking-tight">Resume generator</h1>
      <p className="mt-2 text-muted-foreground">
        Paste a job description — we tailor your resume using your saved profile and your chosen
        format (set it on the <Link href="/ats/format" className="text-primary underline">ATS format</Link>{" "}
        page; upload a resume on your <Link href="/profile" className="text-primary underline">Profile</Link>).
      </p>
      <div className="mt-8 grid gap-4">
        <button
          onClick={onJd}
          className="group rounded-2xl border border-border bg-card p-6 text-left shadow-card transition-all hover:border-primary/40 hover:shadow-card-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <FileText className="h-7 w-7 text-primary" aria-hidden />
          <p className="mt-3 text-lg font-semibold">Paste a job description</p>
          <p className="mt-1 text-sm text-muted-foreground">
            Tailor your resume to a specific posting. Your format choice decides how — AI-generated
            from your profile, or in-place keyword edits on your saved resume.
          </p>
          <span className="mt-4 inline-flex items-center text-sm font-medium text-primary">
            Start →
          </span>
        </button>
      </div>
    </div>
  );
}

// ─── Step: JD entry ─────────────────────────────────────────────────────────

function JdEntry({
  value,
  onChange,
  onBack,
  onContinue,
}: {
  value: string;
  onChange: (v: string) => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const [cov, setCov] = useState<{ percent: number; matched: string[] } | null>(null);
  const [checking, setChecking] = useState(false);

  async function check() {
    setChecking(true);
    try {
      const c = await keywordCoverage(value);
      setCov({ percent: c.percent, matched: c.matched });
    } catch {
      /* non-fatal */
    } finally {
      setChecking(false);
    }
  }

  return (
    <StepShell title="Paste the job description" onBack={onBack}>
      <Textarea
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setCov(null);
        }}
        rows={10}
        placeholder="Paste the full job description here…"
        className="text-sm"
      />
      <div className="mt-2 flex items-center justify-between">
        <span className="text-xs text-muted-foreground">{value.trim().length} characters</span>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            disabled={checking || value.trim().length < 200}
            onClick={() => void check()}
          >
            {checking ? "Checking…" : "Check coverage"}
          </Button>
          <Button disabled={value.trim().length < 200} onClick={onContinue}>
            Continue →
          </Button>
        </div>
      </div>
      {cov && (
        <div className="mt-4 rounded-lg border border-border bg-card p-4">
          <p className="text-sm font-medium">
            JD keyword coverage (your current profile):{" "}
            <span className="text-primary">{cov.percent}%</span>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            This is a real keyword-overlap measure, not an invented score. Tailoring should raise
            it.
          </p>
          {cov.matched.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {cov.matched.map((k) => (
                <Badge key={k} variant="secondary">
                  {k}
                </Badge>
              ))}
            </div>
          )}
        </div>
      )}
    </StepShell>
  );
}

// ─── Step: upload entry ─────────────────────────────────────────────────────

function UploadEntry({
  busy,
  uploadKind,
  uploadName,
  pdfNotice,
  jdText,
  onJdChange,
  onPick,
  onConfirmPdf,
  onBack,
  onContinue,
}: {
  busy: boolean;
  uploadKind: UploadKind | null;
  uploadName: string | null;
  pdfNotice: boolean;
  jdText: string;
  onJdChange: (v: string) => void;
  onPick: (f: File) => void;
  onConfirmPdf: () => void;
  onBack: () => void;
  onContinue: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  if (pdfNotice) {
    return (
      <StepShell title="Heads up about PDF uploads" onBack={onBack}>
        <p className="text-sm text-foreground/90">
          PDF uploads can&apos;t preserve exact formatting. We&apos;ll extract your content and
          generate a new resume in your choice of format. Continue?
        </p>
        <div className="mt-4 flex gap-2">
          <Button variant="ghost" onClick={onBack}>
            Cancel
          </Button>
          <Button onClick={onConfirmPdf}>Continue</Button>
        </div>
      </StepShell>
    );
  }
  if (!uploadKind) {
    return (
      <StepShell title="Upload your resume" onBack={onBack}>
        <button
          onClick={() => inputRef.current?.click()}
          className="flex w-full flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed border-border bg-card/50 p-10 text-center transition-colors hover:border-primary/40 hover:bg-primary-soft/30"
        >
          <Upload className="h-8 w-8 text-muted-foreground" aria-hidden />
          <span className="text-sm font-medium">Click to upload a DOCX or PDF</span>
          <span className="text-xs text-muted-foreground">Max 5 MB</span>
          {busy && <span className="text-xs text-primary">Uploading…</span>}
        </button>
        <input
          ref={inputRef}
          type="file"
          accept=".docx,.pdf"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onPick(e.target.files[0])}
        />
      </StepShell>
    );
  }
  // DOCX uploaded (or PDF confirmed) → ask for the target JD.
  return (
    <StepShell title="What job are you targeting?" onBack={onBack}>
      {uploadName && (
        <p className="mb-3 text-xs text-muted-foreground">
          Using <span className="font-medium text-foreground">{uploadName}</span>
          {uploadKind === "docx" ? " — your original formatting will be preserved." : ""}
        </p>
      )}
      <Textarea
        value={jdText}
        onChange={(e) => onJdChange(e.target.value)}
        rows={10}
        placeholder="Paste the job description you're tailoring toward…"
        className="text-sm"
      />
      <div className="mt-2 flex items-center justify-end">
        <Button disabled={jdText.trim().length < 200} onClick={onContinue}>
          Continue →
        </Button>
      </div>
    </StepShell>
  );
}

// ─── Step 2: 6 questions ──────────────────────────────────────────────────────

function Questions({
  answers,
  onChange,
  skills,
  roles,
  onBack,
  onContinue,
  busy,
  defaultFormat,
  overrideFormat,
  onOverrideFormat,
  showDocxNote,
}: {
  answers: AtsQuestions;
  onChange: (a: AtsQuestions) => void;
  skills: string[];
  roles: string[];
  onBack: () => void;
  onContinue: () => void;
  busy: boolean;
  defaultFormat: string;
  overrideFormat: AtsFormat | null;
  onOverrideFormat: (f: AtsFormat | null) => void;
  showDocxNote: boolean;
}) {
  const [showOverride, setShowOverride] = useState(false);
  const FORMATS: AtsFormat[] = ["modern", "classic", "minimal", "plain", "custom"];
  const set = <K extends keyof AtsQuestions>(k: K, v: AtsQuestions[K]) =>
    onChange({ ...answers, [k]: v });
  const toggle = (k: "skills" | "roles", v: string) => {
    const cur = answers[k];
    set(k, cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]);
  };
  return (
    <StepShell title="Customize your resume" onBack={onBack}>
      <div className="space-y-6">
        <QRow label="Page length">
          <Segmented options={[["1", "1 page"], ["2", "2 pages"]]} value={answers.length} onChange={(v) => set("length", v as "1" | "2")} />
        </QRow>
        <QRow label="Tone">
          <Segmented
            options={[["formal", "Formal"], ["confident", "Confident"], ["conversational", "Conversational"]]}
            value={answers.tone}
            onChange={(v) => set("tone", v as AtsQuestions["tone"])}
          />
        </QRow>
        <QRow label="Emphasis">
          <Segmented
            options={[["technical", "Technical depth"], ["leadership", "Leadership impact"], ["execution", "Project execution"], ["mixed", "Mixed/balanced"]]}
            value={answers.emphasis}
            onChange={(v) => set("emphasis", v as AtsQuestions["emphasis"])}
          />
        </QRow>
        <QRow label="Skills to highlight" hint="Pick 3–6 to foreground. Leave empty for AI to choose based on the JD.">
          <Chips items={skills} selected={answers.skills} onToggle={(v) => toggle("skills", v)} empty="Add skills on your profile to pick here." />
        </QRow>
        <QRow label="Roles to emphasize" hint="Leave empty for AI to choose.">
          <Chips items={roles} selected={answers.roles} onToggle={(v) => toggle("roles", v)} empty="Add experience on your profile to pick here." />
        </QRow>
        <QRow label="Anything else?">
          <Textarea
            value={answers.additional}
            onChange={(e) => set("additional", e.target.value)}
            rows={3}
            placeholder="Optional. e.g., 'Avoid mentioning my 2019 internship, emphasize remote leadership in last role.'"
            className="text-sm"
          />
        </QRow>
      </div>

      {/* FIX 6: default format applied automatically; override for this run. */}
      {showDocxNote ? (
        <p className="mt-6 rounded-lg border border-primary/15 bg-primary-soft/40 px-3 py-2 text-sm text-muted-foreground">
          Using your saved resume&apos;s original format — keywords are woven into the existing
          text in place; your formatting and layout are preserved. These answers steer which
          keywords to favour (they don&apos;t add new content).
        </p>
      ) : (
        <div className="mt-6 rounded-lg border border-border px-3 py-2 text-sm">
          <span className="text-muted-foreground">
            Using your default format:{" "}
            <span className="font-medium text-foreground">
              {overrideFormat
                ? overrideFormat.charAt(0).toUpperCase() + overrideFormat.slice(1)
                : defaultFormat}
            </span>
            .
          </span>{" "}
          <button
            type="button"
            onClick={() => setShowOverride((v) => !v)}
            className="font-medium text-primary underline-offset-4 hover:underline"
          >
            Use a different format this time ▾
          </button>
          {showOverride && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {FORMATS.map((f) => (
                <button
                  key={f}
                  type="button"
                  onClick={() => onOverrideFormat(f)}
                  className={cn(
                    "rounded-full border px-3 py-1 text-xs font-medium",
                    overrideFormat === f
                      ? "border-primary bg-primary-soft text-primary-soft-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
              {overrideFormat && (
                <button
                  type="button"
                  onClick={() => onOverrideFormat(null)}
                  className="rounded-full px-3 py-1 text-xs text-muted-foreground underline-offset-4 hover:underline"
                >
                  Reset to default
                </button>
              )}
            </div>
          )}
        </div>
      )}

      <div className="mt-4 flex justify-end">
        <Button disabled={busy} onClick={onContinue}>
          {busy ? "Generating…" : "Generate resume →"}
        </Button>
      </div>
    </StepShell>
  );
}

// ─── Step 3: format ───────────────────────────────────────────────────────────

function FormatStep({
  isDocxPath,
  format,
  setFormat,
  custom,
  setCustom,
  busy,
  onBack,
  onGenerate,
  onNewVersion,
}: {
  isDocxPath: boolean;
  format: AtsFormat;
  setFormat: (f: AtsFormat) => void;
  custom: AtsCustomOptions;
  setCustom: (c: AtsCustomOptions) => void;
  busy: boolean;
  onBack: () => void;
  onGenerate: () => void;
  onNewVersion: () => void;
}) {
  if (isDocxPath) {
    return (
      <StepShell title="Format" onBack={onBack}>
        <div className="rounded-xl border border-primary/20 bg-primary-soft/40 p-4 text-sm">
          Using your original format — we&apos;ll inject keywords in place and preserve your
          exact styling.{" "}
          <button onClick={onNewVersion} className="font-medium text-primary underline-offset-4 hover:underline">
            Want a different format? Generate a new version
          </button>
        </div>
        <div className="mt-6 flex justify-end">
          <Button disabled={busy} onClick={onGenerate}>
            {busy ? "Starting…" : "Generate resume →"}
          </Button>
        </div>
      </StepShell>
    );
  }
  return (
    <StepShell title="Choose a format" onBack={onBack}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {FORMATS.map((f) => (
          <button
            key={f.id}
            onClick={() => setFormat(f.id)}
            className={cn(
              "rounded-xl border p-3 text-left transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              format === f.id ? "border-primary ring-1 ring-primary" : "border-border hover:border-primary/40",
            )}
          >
            <FormatThumb id={f.id} />
            <p className="mt-2 text-sm font-semibold">{f.name}</p>
            <p className="text-xs text-muted-foreground">{f.blurb}</p>
          </button>
        ))}
      </div>

      <div className={cn("mt-3 rounded-xl border p-4", format === "custom" ? "border-primary" : "border-border")}>
        <button onClick={() => setFormat("custom")} className="text-sm font-semibold">
          Custom
        </button>
        <p className="text-xs text-muted-foreground">Lightweight tweaks on a base format — not a full template editor.</p>
        {format === "custom" && (
          <div className="mt-3 space-y-3">
            <Field label="Base">
              <Segmented options={FORMATS.filter((f) => f.id !== "custom").map((f) => [f.id, f.name] as [string, string])} value={custom.base} onChange={(v) => setCustom({ ...custom, base: v as AtsFormat })} />
            </Field>
            <Field label="Accent">
              <div className="flex gap-2">
                {ACCENTS.map((a) => (
                  <button
                    key={a}
                    aria-label={a}
                    onClick={() => setCustom({ ...custom, accent_color: a })}
                    className={cn("h-7 w-7 rounded-full border-2", custom.accent_color === a ? "border-foreground" : "border-transparent")}
                    style={{ background: ACCENT_HEX[a] }}
                  />
                ))}
              </div>
            </Field>
            <Field label="Font">
              <Segmented options={[["sans", "Sans-serif"], ["serif", "Serif"]]} value={custom.font_family} onChange={(v) => setCustom({ ...custom, font_family: v as "sans" | "serif" })} />
            </Field>
            <Field label="Margins">
              <Segmented options={[["normal", "Normal"], ["tight", "Tight"], ["loose", "Loose"]]} value={custom.margins} onChange={(v) => setCustom({ ...custom, margins: v as AtsCustomOptions["margins"] })} />
            </Field>
          </div>
        )}
      </div>

      <div className="mt-6 flex justify-end">
        <Button disabled={busy} onClick={onGenerate}>
          {busy ? "Starting…" : "Generate resume →"}
        </Button>
      </div>
    </StepShell>
  );
}

const ACCENT_HEX: Record<string, string> = {
  blue: "#1E6FE0",
  slate: "#334155",
  teal: "#0F766E",
  plum: "#6D28D9",
  none: "#222222",
};

function FormatThumb({ id }: { id: AtsFormat }) {
  // Lightweight CSS mock of each layout (generic — swap for real screenshots).
  const accent = id === "modern" ? "bg-primary" : "bg-foreground/70";
  const serif = id === "classic";
  return (
    <div className="flex h-20 flex-col gap-1 rounded-md border border-border bg-background p-2" aria-hidden>
      <div className={cn("h-2 w-2/3 rounded", accent, serif && "italic")} />
      <div className="h-1 w-1/3 rounded bg-muted-foreground/40" />
      <div className="mt-1 h-px w-full bg-border" />
      {[0, 1, 2].map((i) => (
        <div key={i} className="h-1 rounded bg-muted-foreground/25" style={{ width: `${90 - i * 15}%` }} />
      ))}
    </div>
  );
}

// ─── Step 4/5: preview + download ─────────────────────────────────────────────

function Preview({
  run,
  format,
  custom,
  runId,
  onStartOver,
}: {
  run: AtsRun | null;
  format: AtsFormat;
  custom: AtsCustomOptions;
  runId: string | null;
  onStartOver: () => void;
}) {
  if (!run || run.status === "analyzing" || run.status === "generating") {
    const partial = run?.resume;
    return (
      <StepShell title="Generating your resume…" onBack={onStartOver} backLabel="Cancel">
        {partial ? <ResumeView resume={partial} /> : <PreviewSkeleton />}
      </StepShell>
    );
  }
  if (run.status === "error") {
    return (
      <StepShell title="Something went wrong" onBack={onStartOver} backLabel="Start over">
        <p className="text-sm text-destructive">{run.error || "Generation failed."}</p>
      </StepShell>
    );
  }
  // Done.
  if (run.diff) {
    return (
      <DiffView
        diff={run.diff}
        demoMode={run.demo_mode}
        runId={runId!}
        onStartOver={onStartOver}
      />
    );
  }
  return (
    <StepShell title="Your tailored resume" onBack={onStartOver} backLabel="Start over">
      {run.demo_mode && (
        <Badge variant="muted" className="mb-3">
          demo mode — set ANTHROPIC_API_KEY for live generation
        </Badge>
      )}
      {run.coverage && (
        <div className="mb-3 rounded-lg border border-primary/20 bg-primary-soft/40 p-3 text-sm">
          JD keyword coverage after tailoring:{" "}
          <span className="font-semibold text-primary">{run.coverage.percent}%</span>
          {run.coverage.matched.length > 0 && (
            <span className="text-muted-foreground"> · {run.coverage.matched.length} keywords matched</span>
          )}
        </div>
      )}
      {run.resume && <ResumeView resume={run.resume} />}
      {run.resume && (
        <GenerateDownload resume={run.resume} format={format} custom={custom} />
      )}
    </StepShell>
  );
}

function GenerateDownload({
  resume,
  format,
  custom,
}: {
  resume: TailoredResume;
  format: AtsFormat;
  custom: AtsCustomOptions;
}) {
  const [downloading, setDownloading] = useState<"docx" | "pdf" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  async function dl(fileFmt: "docx" | "pdf") {
    setDownloading(fileFmt);
    setErr(null);
    try {
      const blob = await downloadResume(
        resume,
        "tailored-resume",
        fileFmt,
        "visual",
        "center",
        format,
        format === "custom" ? custom : null,
      );
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `tailored-resume.${fileFmt}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(null);
    }
  }
  return (
    <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-border pt-4">
      <Button disabled={downloading !== null} onClick={() => void dl("docx")}>
        {downloading === "docx" ? "Preparing…" : "Download DOCX"}
      </Button>
      <Button variant="outline" disabled={downloading !== null} onClick={() => void dl("pdf")}>
        {downloading === "pdf" ? "Preparing…" : "Download PDF"}
      </Button>
      <span className="text-xs text-muted-foreground">Format: {format}</span>
      {err && <span className="text-xs text-destructive">{err}</span>}
    </div>
  );
}

function DiffView({
  diff,
  demoMode,
  runId,
  onStartOver,
}: {
  diff: { applied: { original_text: string; replacement_text: string }[]; skipped: { original_text: string }[] };
  demoMode: boolean;
  runId: string;
  onStartOver: () => void;
}) {
  const [accepted, setAccepted] = useState<boolean[]>(diff.applied.map(() => true));
  const [filename, setFilename] = useState("");
  const [downloading, setDownloading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function dl() {
    setDownloading(true);
    setErr(null);
    try {
      const idx = accepted.flatMap((a, i) => (a ? [i] : []));
      const blob = await downloadAtsDocx(runId, idx, filename.trim() || undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      const out = filename.trim().replace(/\.docx$/i, "");
      a.download = `${out || "resume-tailored"}.docx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }
  return (
    <StepShell title="Review keyword changes" onBack={onStartOver} backLabel="Start over">
      {diff.applied.length > 0 && (
        <p className="mb-3 text-sm text-muted-foreground">
          {diff.applied.length} in-place keyword edit{diff.applied.length === 1 ? "" : "s"} applied —
          wording only; your formatting, sections, and layout are unchanged. Review and download below.
        </p>
      )}
      {diff.applied.length === 0 && (
        <p className="text-sm text-muted-foreground">
          {demoMode
            ? "Demo mode — set ANTHROPIC_API_KEY for live keyword edits. Your original document is unchanged."
            : "No safe in-place keyword swaps were found for this job. Your original document is unchanged."}
        </p>
      )}
      <ul className="space-y-2">
        {diff.applied.map((e, i) => (
          <li key={i} className="flex items-start gap-3 rounded-lg border border-border p-3">
            <button
              aria-label={accepted[i] ? "Reject change" : "Accept change"}
              onClick={() => setAccepted((a) => a.map((v, j) => (j === i ? !v : v)))}
              className={cn(
                "mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded border",
                accepted[i] ? "border-primary bg-primary text-primary-foreground" : "border-border",
              )}
            >
              {accepted[i] && <Check className="h-3.5 w-3.5" />}
            </button>
            <div className="grid flex-1 gap-1 text-sm sm:grid-cols-2">
              <span className="text-muted-foreground line-through">{e.original_text}</span>
              <span className="text-foreground">{e.replacement_text}</span>
            </div>
          </li>
        ))}
      </ul>
      {diff.skipped.length > 0 && (
        <div className="mt-4">
          <p className="text-xs font-medium text-muted-foreground">
            Couldn&apos;t place {diff.skipped.length} suggested change(s) (the exact phrase
            wasn&apos;t found in the document) — left out:
          </p>
          <ul className="mt-1 space-y-1">
            {diff.skipped.map((e, i) => (
              <li key={i} className="rounded border border-dashed border-border px-2 py-1 text-xs text-muted-foreground line-through">
                {e.original_text}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-5 border-t border-border pt-4">
        <label className="block text-sm font-medium" htmlFor="docx-name">
          Output file name
        </label>
        <div className="mt-1 flex flex-wrap items-center gap-2">
          <div className="flex items-center">
            <input
              id="docx-name"
              type="text"
              value={filename}
              onChange={(e) => setFilename(e.target.value)}
              placeholder="resume-tailored"
              className="w-56 rounded-md border border-border bg-background px-3 py-1.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            <span className="ml-1 text-sm text-muted-foreground">.docx</span>
          </div>
          <Button disabled={downloading} onClick={() => void dl()}>
            {downloading ? "Preparing…" : "Download DOCX"}
          </Button>
          {err && <span className="text-xs text-destructive">{err}</span>}
        </div>
      </div>
    </StepShell>
  );
}

function ResumeView({ resume }: { resume: TailoredResume }) {
  return (
    <div className="space-y-4 rounded-xl border border-border bg-card p-5">
      <div className="border-b border-border/60 pb-3">
        <p className="text-lg font-bold">{resume.contact.name || "Your Name"}</p>
        {resume.contact.headline && <p className="text-sm text-muted-foreground">{resume.contact.headline}</p>}
      </div>
      {resume.summary && <p className="text-sm text-foreground/90">{resume.summary}</p>}
      {resume.skills.length > 0 && (
        <p className="text-sm">
          {resume.skills.map((g) => (g.category ? `${g.category}: ${g.items.join(", ")}` : g.items.join(", "))).join(" · ")}
        </p>
      )}
      {resume.experience.map((e, i) => (
        <div key={i} className="space-y-1">
          <p className="text-sm font-medium">{e.title}{e.company ? ` · ${e.company}` : ""}</p>
          <ul className="ml-4 list-disc space-y-0.5 text-sm text-foreground/85">
            {e.bullets.map((b, j) => (
              <li key={j}>{b}</li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function PreviewSkeleton() {
  return (
    <div className="space-y-3" aria-hidden>
      <Skeleton className="h-6 w-1/3" />
      <Skeleton className="h-3 w-full" />
      <Skeleton className="h-3 w-5/6" />
      <Skeleton className="h-3 w-4/6" />
    </div>
  );
}

// ─── Shared bits ──────────────────────────────────────────────────────────────

function StepShell({
  title,
  children,
  onBack,
  backLabel = "Back",
}: {
  title: string;
  children: React.ReactNode;
  onBack: () => void;
  backLabel?: string;
}) {
  return (
    <div>
      <button onClick={onBack} className="mb-3 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-3.5 w-3.5" aria-hidden /> {backLabel}
      </button>
      <h1 className="font-display text-2xl font-bold tracking-tight">{title}</h1>
      <div className="mt-5">{children}</div>
    </div>
  );
}

function QRow({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-sm font-semibold">{label}</p>
      {hint && <p className="mb-2 text-xs text-muted-foreground">{hint}</p>}
      <div className={hint ? "" : "mt-2"}>{children}</div>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <span className="w-16 text-xs font-medium text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

function Segmented({
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

function Chips({
  items,
  selected,
  onToggle,
  empty,
}: {
  items: string[];
  selected: string[];
  onToggle: (v: string) => void;
  empty: string;
}) {
  if (items.length === 0) return <p className="text-xs text-muted-foreground">{empty}</p>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((it) => (
        <button
          key={it}
          aria-pressed={selected.includes(it)}
          onClick={() => onToggle(it)}
          className={cn(
            "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
            selected.includes(it)
              ? "border-primary bg-primary-soft text-primary-soft-foreground"
              : "border-border text-muted-foreground hover:text-foreground",
          )}
        >
          {it}
        </button>
      ))}
    </div>
  );
}
