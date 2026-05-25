"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  type Analysis,
  type Job,
  type TailoredResume,
  analyzeJob,
  downloadResumeDocx,
  generateTailoredResume,
} from "@/lib/api";

type Step = "idle" | "analyzing" | "answering" | "generating" | "ready";

export function TailorPanel({ job }: { job: Job }) {
  const [step, setStep] = useState<Step>("idle");
  const [demoMode, setDemoMode] = useState(false);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [resume, setResume] = useState<TailoredResume | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [downloading, setDownloading] = useState(false);

  async function onAnalyze() {
    setError(null);
    setStep("analyzing");
    try {
      const res = await analyzeJob(job.id);
      setAnalysis(res.analysis);
      setDemoMode(res.demo_mode);
      // Seed answer keys from the questions.
      setAnswers(Object.fromEntries(res.analysis.questions.map((q) => [q, ""])));
      setStep("answering");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analyze failed");
      setStep("idle");
    }
  }

  async function onGenerate() {
    if (!analysis) return;
    setError(null);
    setStep("generating");
    try {
      const res = await generateTailoredResume(job.id, answers);
      setResume(res.resume);
      setDemoMode(res.demo_mode);
      setStep("ready");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed");
      setStep("answering");
    }
  }

  async function onDownload() {
    if (!resume) return;
    setDownloading(true);
    setError(null);
    try {
      const filename = `${job.company}-${job.title}`
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80);
      const blob = await downloadResumeDocx(resume, filename || "tailored-resume");
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${filename || "tailored-resume"}.docx`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  function reset() {
    setStep("idle");
    setAnalysis(null);
    setAnswers({});
    setResume(null);
    setError(null);
  }

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-lg">Tailor my resume</CardTitle>
            <CardDescription>
              Score the fit, surface gaps, and produce an ATS-optimized resume
              for this role.
            </CardDescription>
          </div>
          {demoMode && step !== "idle" && (
            <Badge variant="muted" title="ANTHROPIC_API_KEY is not configured">
              demo mode
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {step === "idle" && (
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Uses Claude Sonnet 4.6. Results are cached per job.
            </p>
            <Button onClick={() => void onAnalyze()}>Tailor my resume</Button>
          </div>
        )}

        {step === "analyzing" && <AnalyzeSkeleton />}

        {step !== "idle" && step !== "analyzing" && analysis && (
          <AnalysisView analysis={analysis} />
        )}

        {(step === "answering" || step === "generating") && analysis && (
          <>
            <Separator />
            <div className="space-y-3">
              <h3 className="text-sm font-semibold">
                {analysis.questions.length === 0
                  ? "No gap questions"
                  : `Confirm missing skills (${analysis.questions.length})`}
              </h3>
              <p className="text-xs text-muted-foreground">
                {analysis.questions.length === 0
                  ? "No skills are missing from your resume — go straight to generate."
                  : "Yes/no per skill. Answer affirmatively only if you genuinely have it; we will not invent anything."}
              </p>
              {analysis.questions.map((q, i) => (
                <label key={q} className="block space-y-1">
                  <span className="text-sm font-medium">
                    <span className="text-muted-foreground">{i + 1}.</span> {q}
                  </span>
                  <Textarea
                    rows={2}
                    value={answers[q] ?? ""}
                    onChange={(e) =>
                      setAnswers((a) => ({ ...a, [q]: e.target.value }))
                    }
                    placeholder="Your answer (optional)"
                  />
                </label>
              ))}
              <div className="flex items-center justify-end gap-2">
                <Button variant="ghost" onClick={reset}>
                  Cancel
                </Button>
                <Button
                  onClick={() => void onGenerate()}
                  disabled={step === "generating"}
                >
                  {step === "generating" ? "Generating…" : "Generate resume"}
                </Button>
              </div>
            </div>
          </>
        )}

        {step === "ready" && resume && (
          <>
            <Separator />
            <ResumeView resume={resume} />
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button variant="ghost" onClick={reset}>
                Start over
              </Button>
              <Button onClick={() => void onDownload()} disabled={downloading}>
                {downloading ? "Preparing…" : "Download .docx"}
              </Button>
            </div>
          </>
        )}

        {error && (
          <p
            role="alert"
            className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
          >
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function AnalyzeSkeleton() {
  return (
    <div className="space-y-3" aria-busy="true">
      <div className="flex items-center gap-3">
        <Skeleton className="h-16 w-16 rounded-full" />
        <div className="flex-1 space-y-2">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-3 w-40" />
        </div>
      </div>
      <div className="flex gap-2">
        <Skeleton className="h-5 w-16 rounded-full" />
        <Skeleton className="h-5 w-20 rounded-full" />
        <Skeleton className="h-5 w-24 rounded-full" />
      </div>
      <Skeleton className="h-16 w-full" />
    </div>
  );
}

function AnalysisView({ analysis }: { analysis: Analysis }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <ScoreRing score={analysis.match_score} />
        <div>
          <p className="text-sm font-medium">Match score</p>
          <p className="text-xs text-muted-foreground">
            Higher is better. Driven by overlap between the JD and your profile.
          </p>
        </div>
      </div>

      {analysis.top_skills.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            JD emphasizes
          </p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.top_skills.map((s) => (
              <Badge key={s} variant="default">
                {s}
              </Badge>
            ))}
          </div>
        </div>
      )}

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Matched
          </p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.matched.length === 0 ? (
              <span className="text-xs text-muted-foreground">None detected.</span>
            ) : (
              analysis.matched.map((s) => (
                <Badge key={s} variant="secondary">
                  {s}
                </Badge>
              ))
            )}
          </div>
        </div>
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Gaps (askable)
          </p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.gaps.length === 0 ? (
              <span className="text-xs text-muted-foreground">None detected.</span>
            ) : (
              analysis.gaps.map((s) => (
                <Badge key={s} variant="outline">
                  {s}
                </Badge>
              ))
            )}
          </div>
        </div>
      </div>

      {analysis.genuine_lacks && analysis.genuine_lacks.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
            Genuine lacks
          </p>
          <p className="text-xs text-muted-foreground">
            JD requirements no answer would change — surfaced honestly.
          </p>
          <div className="flex flex-wrap gap-1.5">
            {analysis.genuine_lacks.map((s) => (
              <Badge key={s} variant="destructive">
                {s}
              </Badge>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ScoreRing({ score }: { score: number }) {
  // Inline conic-gradient ring — no chart dep needed.
  const safe = Math.max(0, Math.min(100, score));
  const color =
    safe >= 75
      ? "hsl(var(--primary))"
      : safe >= 50
        ? "hsl(var(--highlight))"
        : "hsl(var(--muted-foreground))";
  return (
    <div
      role="img"
      aria-label={`Match score ${safe} out of 100`}
      className="grid h-16 w-16 place-items-center rounded-full"
      style={{
        background: `conic-gradient(${color} ${safe * 3.6}deg, hsl(var(--secondary)) 0)`,
      }}
    >
      <div className="grid h-12 w-12 place-items-center rounded-full bg-card font-semibold">
        {safe}
      </div>
    </div>
  );
}

function ResumeView({ resume }: { resume: TailoredResume }) {
  return (
    <div className="space-y-5">
      <section className="rounded-md border border-highlight/20 bg-highlight-soft/40 p-3">
        <p className="text-[11px] font-semibold uppercase tracking-wider text-highlight-foreground">
          ATS notes
        </p>
        <p className="mt-1 text-sm text-foreground/90">{resume.ats_notes}</p>
      </section>

      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Summary
        </p>
        <p className="mt-1.5 text-sm leading-relaxed text-foreground/90">
          {resume.summary}
        </p>
      </section>

      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Skills
        </p>
        <div className="mt-1.5 flex flex-wrap gap-1">
          {resume.skills.map((s) => (
            <span
              key={s}
              className="rounded-md bg-secondary px-1.5 py-0.5 text-xs font-medium text-secondary-foreground"
            >
              {s}
            </span>
          ))}
        </div>
      </section>

      <section className="space-y-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Experience
        </p>
        {resume.experience.map((e, i) => (
          <div key={`${e.company}-${i}`} className="space-y-1">
            <p className="text-sm font-medium">
              {e.title}, {e.company}
              <span className="ml-2 text-xs font-normal text-muted-foreground">
                {e.location ? `${e.location} · ` : ""}
                {e.dates}
              </span>
            </p>
            <ul className="ml-4 list-disc space-y-1 text-sm text-foreground/90">
              {e.bullets.map((b, j) => (
                <li key={j}>{b}</li>
              ))}
            </ul>
          </div>
        ))}
      </section>

      <section>
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Education
        </p>
        <ul className="mt-1.5 space-y-1 text-sm text-foreground/90">
          {resume.education.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
