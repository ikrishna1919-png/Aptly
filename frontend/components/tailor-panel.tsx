"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  type Job,
  type ResumeMode,
  type TailoredResume,
  type TailorRunState,
  type TailorRunStatus,
  ProfileThinError,
  TAILOR_MAX_WAIT_MS,
  TAILOR_POLL_INTERVAL_MS,
  downloadResume,
  fetchTailorRun,
  startTailor,
  submitTailorAnswers,
} from "@/lib/api";
import { useAuthGate } from "@/lib/use-login-modal";

// One answer to a gap question. `verdict` drives whether the skill is
// confirmed; `detail` is the optional "tell me more" the user can add when
// they answer Yes / A little. Serialized into the string the backend's
// affirmative check + exclusion list understand.
type Verdict = "yes" | "a_little" | "no" | "skip";
type Answer = { verdict: Verdict; detail: string };

const VERDICTS: { value: Verdict; label: string }[] = [
  { value: "yes", label: "Yes" },
  { value: "a_little", label: "A little" },
  { value: "no", label: "No" },
  { value: "skip", label: "Skip" },
];

/** Serialize a verdict + detail into the answer string the backend reads.
 * "no"/"skip" → "" / "No" (treated as not-confirmed); affirmatives carry the
 * optional detail so the model has context. Honest + non-leading: we never
 * inject a number or duration the user didn't volunteer. */
function serializeAnswer(a: Answer): string {
  if (a.verdict === "no") return "No";
  if (a.verdict === "skip") return "";
  const lead = a.verdict === "yes" ? "Yes" : "A little";
  return a.detail.trim() ? `${lead} — ${a.detail.trim()}` : lead;
}

export function TailorPanel({ job }: { job: Job }) {
  const gate = useAuthGate();
  const reduce = useReducedMotion();

  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<TailorRunState | null>(null);
  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [thin, setThin] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // The editable working copy of the AI result + the pristine original (for
  // "Reset to AI version"). Both null until generation completes.
  const [draft, setDraft] = useState<TailoredResume | null>(null);
  const [original, setOriginal] = useState<TailoredResume | null>(null);

  const startedAtRef = useRef<number>(0);
  // ── Latency instrumentation ──────────────────────────────────────────────
  // Marks are relative to the click (t=0) and logged to the console so the
  // end-to-end timeline (click → run_id → first byte → first visible char →
  // complete) can be read off without a profiler. Cheap; ~6 logs per run.
  const timeOriginRef = useRef<number>(0);
  const markedRef = useRef<Set<string>>(new Set());
  const mark = useCallback((label: string) => {
    if (markedRef.current.has(label)) return;
    markedRef.current.add(label);
    const dt = Math.round(performance.now() - timeOriginRef.current);
    // eslint-disable-next-line no-console
    console.log(`[tailor-timing] ${label} +${dt}ms`);
  }, []);

  const status: TailorRunStatus | "idle" = run?.status ?? (runId ? "analyzing" : "idle");
  const demoMode = run?.demo_mode ?? false;
  // Once any section has streamed in, the content itself is the progress
  // indicator — we drop the spinner (honest UI: don't fake "Generating…"
  // over visible output).
  const hasStreamedContent = !!(
    run?.resume &&
    (run.resume.summary || run.resume.skills?.length || run.resume.experience?.length)
  );

  const begin = useCallback(
    async (force: boolean) => {
      timeOriginRef.current = performance.now();
      markedRef.current = new Set();
      mark("ui.click");
      setStarting(true);
      setError(null);
      setThin(null);
      setRun(null);
      setDraft(null);
      setOriginal(null);
      try {
        const { run_id } = await startTailor(job.id, force);
        mark("ui.post_sent (run_id received)");
        startedAtRef.current = Date.now();
        setRunId(run_id);
      } catch (e) {
        if (e instanceof ProfileThinError) setThin(e.message);
        else setError(e instanceof Error ? e.message : "Couldn't start tailoring");
      } finally {
        setStarting(false);
      }
    },
    [job.id, mark],
  );

  // Emit timing marks as the run advances through its lifecycle.
  useEffect(() => {
    if (!run) return;
    mark("ui.first_byte (first poll response)");
    if (run.status === "pending_questions" && run.analysis) mark("ui.questions_shown");
    if (run.status === "generating" && run.resume?.summary) {
      mark("ui.first_render (first streamed section)");
    }
    if (run.status === "done") mark("ui.complete (form populated)");
  }, [run, mark]);

  // Poll the run while it's actively working. Re-runs after each poll (state
  // change) until the status is terminal (done/error) or waiting on the user
  // (pending_questions). A 120s deadline guards against a stuck worker.
  useEffect(() => {
    if (!runId) return;
    const working = run === null || run.status === "analyzing" || run.status === "generating";
    if (!working) return;
    if (run !== null && Date.now() - startedAtRef.current > TAILOR_MAX_WAIT_MS) {
      setError(
        "This is taking longer than expected. Try again, or contact support if it persists.",
      );
      setRunId(null);
      return;
    }
    let cancelled = false;
    const delay = run === null ? 0 : TAILOR_POLL_INTERVAL_MS;
    const t = setTimeout(async () => {
      try {
        const next = await fetchTailorRun(runId);
        if (!cancelled) setRun(next);
      } catch {
        if (!cancelled) {
          // Transient network blip (e.g. cold start) — keep the run id and let
          // the effect retry on the next tick by nudging state.
          setRun((prev) => (prev ? { ...prev } : prev));
        }
      }
    }, delay);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [runId, run]);

  // Seed the editable draft once generation completes.
  useEffect(() => {
    if (run?.status === "done" && run.resume && !original) {
      setOriginal(run.resume);
      setDraft(run.resume);
    }
  }, [run, original]);

  // Seed answer slots when questions arrive.
  useEffect(() => {
    if (run?.status === "pending_questions" && run.analysis) {
      setAnswers((prev) => {
        if (Object.keys(prev).length) return prev;
        return Object.fromEntries(
          run.analysis!.questions.map((q) => [q, { verdict: "skip", detail: "" } as Answer]),
        );
      });
    }
  }, [run]);

  async function onSubmitAnswers() {
    if (!runId || !run?.analysis) return;
    setError(null);
    const payload: Record<string, string> = {};
    for (const q of run.analysis.questions) {
      payload[q] = serializeAnswer(answers[q] ?? { verdict: "skip", detail: "" });
    }
    try {
      await submitTailorAnswers(runId, payload);
      mark("ui.answers_submitted (generate leg starts)");
      startedAtRef.current = Date.now();
      // Optimistically flip to generating so the poller resumes.
      setRun((prev) => (prev ? { ...prev, status: "generating", resume: null } : prev));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't submit answers");
    }
  }

  async function onRetry() {
    if (!runId || !run?.analysis) {
      void begin(true);
      return;
    }
    await onSubmitAnswers();
  }

  function reset() {
    setRunId(null);
    setRun(null);
    setAnswers({});
    setThin(null);
    setError(null);
    setDraft(null);
    setOriginal(null);
  }

  const slide = reduce
    ? {}
    : { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, exit: { opacity: 0, y: -8 } };

  return (
    <Card className="overflow-hidden">
      <CardHeader>
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-lg">Tailor my resume</CardTitle>
            <CardDescription>
              We compare your profile to this job, confirm anything that&apos;s
              missing, and write an ATS-ready resume grounded in your real
              experience.
            </CardDescription>
          </div>
          {demoMode && status !== "idle" && (
            <Badge variant="muted" title="ANTHROPIC_API_KEY is not configured">
              demo mode
            </Badge>
          )}
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        <AnimatePresence mode="wait" initial={false}>
          {/* Idle / thin-profile intro */}
          {status === "idle" && (
            <motion.div key="idle" {...slide} className="space-y-3">
              {thin ? (
                <ThinProfileNotice
                  message={thin}
                  onGenerateAnyway={() => void begin(true)}
                  busy={starting}
                />
              ) : (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-muted-foreground">
                    Grounded in your real profile — we never invent skills or
                    numbers you don&apos;t have.
                  </p>
                  <Button
                    disabled={starting}
                    onClick={() => {
                      if (gate("tailor")) void begin(false);
                    }}
                  >
                    {starting ? "Starting…" : "Tailor my resume"}
                  </Button>
                </div>
              )}
            </motion.div>
          )}

          {/* Analyzing */}
          {status === "analyzing" && (
            <motion.div key="analyzing" {...slide}>
              <WorkingState
                title="Analyzing the job description…"
                subtitle="Comparing it against your profile to spot anything worth confirming."
              />
            </motion.div>
          )}

          {/* Questions */}
          {status === "pending_questions" && run?.analysis && (
            <motion.div key="questions" {...slide} className="space-y-4">
              <QuestionsForm
                questions={run.analysis.questions}
                answers={answers}
                onChange={setAnswers}
              />
              <MatchedKeywords matched={run.analysis.matched} />
              <GenuineLacks lacks={run.analysis.genuine_lacks ?? []} />
              <div className="flex items-center justify-end gap-2">
                <Button variant="ghost" onClick={reset}>
                  Cancel
                </Button>
                <Button onClick={() => void onSubmitAnswers()}>Generate resume</Button>
              </div>
            </motion.div>
          )}

          {/* Generating — the streamed content IS the progress indicator. The
              spinner/hint shows ONLY until the first section lands; after that
              we just render the resume as it fills in. */}
          {status === "generating" && (
            <motion.div key="generating" {...slide}>
              {hasStreamedContent ? (
                <ProgressReveal resume={run?.resume ?? null} />
              ) : (
                <WorkingState
                  title="Tailoring your resume…"
                  subtitle="Writing each section from your confirmed experience."
                />
              )}
            </motion.div>
          )}

          {/* Ready — editable preview */}
          {status === "done" && draft && (
            <motion.div key="ready" {...slide} className="space-y-4">
              {run?.cached && (
                <p className="rounded-md border border-primary/15 bg-primary-soft/40 px-3 py-2 text-xs text-muted-foreground">
                  Loaded your recent version for this job instantly. Edit it below and
                  download — your changes are kept.
                </p>
              )}
              {run?.resume && <MatchedKeywords matched={run.resume.ats.matched_keywords} />}
              <EditableResume value={draft} onChange={setDraft} />
              <Separator />
              <ResumeFooter
                resume={draft}
                canReset={original !== null && draft !== original}
                onReset={() => original && setDraft(original)}
                onStartOver={reset}
                job={job}
              />
            </motion.div>
          )}

          {/* Error */}
          {status === "error" && (
            <motion.div key="error" {...slide} className="space-y-3">
              <p
                role="alert"
                className="rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive"
              >
                {run?.error || "Generation incomplete."}
              </p>
              <div className="flex items-center justify-end gap-2">
                <Button variant="ghost" onClick={reset}>
                  Start over
                </Button>
                <Button onClick={() => void onRetry()}>Try again</Button>
              </div>
            </motion.div>
          )}
        </AnimatePresence>

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

// ─── Sub-views ──────────────────────────────────────────────────────────────

function ThinProfileNotice({
  message,
  onGenerateAnyway,
  busy,
}: {
  message: string;
  onGenerateAnyway: () => void;
  busy: boolean;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-highlight/40 bg-highlight-soft/50 p-4">
      <p className="text-sm text-foreground/90">{message}</p>
      <div className="flex flex-wrap items-center gap-2">
        <Button asChild>
          <a href="/profile">Go to Profile</a>
        </Button>
        <Button variant="outline" disabled={busy} onClick={onGenerateAnyway}>
          {busy ? "Starting…" : "Generate anyway"}
        </Button>
      </div>
    </div>
  );
}

function WorkingState({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div className="flex items-start gap-3" role="status" aria-live="polite">
      <Spinner />
      <div className="space-y-0.5">
        <p className="text-sm font-medium">{title}</p>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="mt-0.5 h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-primary/30 border-t-primary"
    />
  );
}

function QuestionsForm({
  questions,
  answers,
  onChange,
}: {
  questions: string[];
  answers: Record<string, Answer>;
  onChange: (next: Record<string, Answer>) => void;
}) {
  const set = (q: string, patch: Partial<Answer>) =>
    onChange({ ...answers, [q]: { ...(answers[q] ?? { verdict: "skip", detail: "" }), ...patch } });

  return (
    <div className="space-y-3">
      <div className="space-y-1">
        <h3 className="text-sm font-semibold">Confirm what&apos;s missing ({questions.length})</h3>
        <p className="text-xs text-muted-foreground">
          Answer honestly — only confirmed skills make it onto your resume. Anything
          you mark &quot;No&quot; or skip is left off.
        </p>
      </div>
      {questions.map((q, i) => {
        const a = answers[q] ?? { verdict: "skip" as Verdict, detail: "" };
        const showDetail = a.verdict === "yes" || a.verdict === "a_little";
        return (
          <div key={q} className="space-y-2 rounded-lg border border-border/70 p-3">
            <p className="text-sm font-medium">
              <span className="text-muted-foreground">{i + 1}.</span> {q}
            </p>
            <div
              role="radiogroup"
              aria-label={q}
              className="inline-flex flex-wrap gap-1 rounded-md border border-border bg-card p-0.5"
            >
              {VERDICTS.map((v) => {
                const active = a.verdict === v.value;
                return (
                  <button
                    key={v.value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => set(q, { verdict: v.value })}
                    className={
                      "rounded px-3 py-1 text-xs font-medium transition-colors " +
                      (active
                        ? "bg-primary text-primary-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground")
                    }
                  >
                    {v.label}
                  </button>
                );
              })}
            </div>
            {showDetail && (
              <Input
                value={a.detail}
                onChange={(e) => set(q, { detail: e.target.value })}
                placeholder="Tell me more (optional) — where or how you used it"
                className="text-sm"
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function MatchedKeywords({ matched }: { matched: string[] }) {
  if (!matched.length) return null;
  return (
    <section className="rounded-md border border-primary/15 bg-primary-soft/40 p-3">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-primary-soft-foreground">
        Keywords from this job already on your resume
      </p>
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {matched.map((s) => (
          <Badge key={s} variant="secondary">
            {s}
          </Badge>
        ))}
      </div>
    </section>
  );
}

function GenuineLacks({ lacks }: { lacks: string[] }) {
  if (!lacks.length) return null;
  return (
    <section className="space-y-1.5">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        Requirements no answer can change
      </p>
      <p className="text-xs text-muted-foreground">
        Surfaced honestly — we won&apos;t add these unless they&apos;re genuinely yours.
      </p>
      <div className="flex flex-wrap gap-1.5">
        {lacks.map((s) => (
          <Badge key={s} variant="outline">
            {s}
          </Badge>
        ))}
      </div>
    </section>
  );
}

/** Read-only progressive view shown while generating: renders whatever
 * sections have streamed in, with a shimmer for what's still coming. */
function ProgressReveal({ resume }: { resume: TailoredResume | null }) {
  const hasSummary = !!resume?.summary;
  const hasSkills = !!resume?.skills?.length;
  const hasExperience = !!resume?.experience?.length;
  return (
    <div className="space-y-4">
      {hasSummary ? (
        <Section label="Professional Summary">
          <p className="text-sm leading-relaxed text-foreground/90">{resume!.summary}</p>
        </Section>
      ) : (
        <ShimmerBlock lines={2} label="Professional Summary" />
      )}
      {hasSkills ? (
        <Section label="Skills">
          {resume!.skills.map((g, i) => (
            <p key={`${g.category}-${i}`} className="text-sm text-foreground/90">
              {g.category && <span className="font-medium">{g.category}: </span>}
              {g.items.join(", ")}
            </p>
          ))}
        </Section>
      ) : hasSummary ? (
        <ShimmerBlock lines={1} label="Skills" />
      ) : null}
      {hasExperience ? (
        <Section label="Experience">
          {resume!.experience.map((e, i) => (
            <div key={`${e.company}-${i}`} className="space-y-1">
              <p className="text-sm font-medium">{e.title}</p>
              <p className="text-xs text-muted-foreground">
                {[e.company, e.location].filter(Boolean).join(", ")}
              </p>
              <ul className="ml-4 list-disc space-y-1 text-sm text-foreground/90">
                {e.bullets.map((b, j) => (
                  <li key={j}>{b}</li>
                ))}
              </ul>
            </div>
          ))}
        </Section>
      ) : hasSkills ? (
        <ShimmerBlock lines={4} label="Experience" />
      ) : null}
    </div>
  );
}

function ShimmerBlock({ lines, label }: { lines: number; label: string }) {
  return (
    <div className="space-y-2" aria-hidden="true">
      <Section label={label}>
        {Array.from({ length: lines }).map((_, i) => (
          <Skeleton key={i} className="h-3.5 w-full" />
        ))}
      </Section>
    </div>
  );
}

function Section({ label, children }: { label: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      {children}
    </section>
  );
}

// ─── Editable resume ──────────────────────────────────────────────────────────

function EditableResume({
  value,
  onChange,
}: {
  value: TailoredResume;
  onChange: (next: TailoredResume) => void;
}) {
  const patch = (p: Partial<TailoredResume>) => onChange({ ...value, ...p });

  return (
    <div className="space-y-5">
      {/* Contact */}
      <Section label="Contact">
        <div className="grid gap-2 sm:grid-cols-2">
          <Field
            label="Name"
            value={value.contact.name}
            onChange={(v) => patch({ contact: { ...value.contact, name: v } })}
          />
          <Field
            label="Headline"
            value={value.contact.headline}
            onChange={(v) => patch({ contact: { ...value.contact, headline: v } })}
          />
          <Field
            label="Location"
            value={value.contact.location}
            onChange={(v) => patch({ contact: { ...value.contact, location: v } })}
          />
          <Field
            label="Email"
            value={value.contact.email}
            onChange={(v) => patch({ contact: { ...value.contact, email: v } })}
          />
        </div>
      </Section>

      {/* Summary */}
      <Section label="Professional Summary">
        <Textarea
          rows={3}
          value={value.summary}
          onChange={(e) => patch({ summary: e.target.value })}
          aria-label="Professional summary"
        />
      </Section>

      {/* Skills */}
      <Section label="Skills">
        <div className="space-y-2">
          {value.skills.map((g, i) => (
            <div key={i} className="grid gap-2 sm:grid-cols-[10rem_1fr]">
              <Input
                value={g.category}
                aria-label={`Skill category ${i + 1}`}
                placeholder="Category"
                onChange={(e) => {
                  const skills = value.skills.slice();
                  skills[i] = { ...g, category: e.target.value };
                  patch({ skills });
                }}
              />
              <Input
                value={g.items.join(", ")}
                aria-label={`${g.category || "Skill"} items`}
                placeholder="Comma-separated skills"
                onChange={(e) => {
                  const skills = value.skills.slice();
                  skills[i] = {
                    ...g,
                    items: e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                  };
                  patch({ skills });
                }}
              />
            </div>
          ))}
        </div>
      </Section>

      {/* Experience */}
      {value.experience.length > 0 && (
        <Section label="Experience">
          <div className="space-y-4">
            {value.experience.map((e, i) => (
              <div key={i} className="space-y-2 rounded-lg border border-border/60 p-3">
                <div className="grid gap-2 sm:grid-cols-2">
                  <Field
                    label="Title"
                    value={e.title}
                    onChange={(v) => {
                      const experience = value.experience.slice();
                      experience[i] = { ...e, title: v };
                      patch({ experience });
                    }}
                  />
                  <Field
                    label="Company"
                    value={e.company}
                    onChange={(v) => {
                      const experience = value.experience.slice();
                      experience[i] = { ...e, company: v };
                      patch({ experience });
                    }}
                  />
                </div>
                <BulletEditor
                  bullets={e.bullets}
                  onChange={(bullets) => {
                    const experience = value.experience.slice();
                    experience[i] = { ...e, bullets };
                    patch({ experience });
                  }}
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Projects */}
      {value.projects.length > 0 && (
        <Section label="Projects">
          <div className="space-y-4">
            {value.projects.map((p, i) => (
              <div key={i} className="space-y-2 rounded-lg border border-border/60 p-3">
                <Field
                  label="Name"
                  value={p.name}
                  onChange={(v) => {
                    const projects = value.projects.slice();
                    projects[i] = { ...p, name: v };
                    patch({ projects });
                  }}
                />
                <Textarea
                  rows={2}
                  value={p.description}
                  aria-label={`Project ${i + 1} description`}
                  onChange={(e) => {
                    const projects = value.projects.slice();
                    projects[i] = { ...p, description: e.target.value };
                    patch({ projects });
                  }}
                />
                <BulletEditor
                  bullets={p.bullets}
                  onChange={(bullets) => {
                    const projects = value.projects.slice();
                    projects[i] = { ...p, bullets };
                    patch({ projects });
                  }}
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Education */}
      {value.education.length > 0 && (
        <Section label="Education">
          <div className="space-y-3">
            {value.education.map((ed, i) => (
              <div key={i} className="grid gap-2 rounded-lg border border-border/60 p-3 sm:grid-cols-2">
                <Field
                  label="Degree"
                  value={ed.degree}
                  onChange={(v) => {
                    const education = value.education.slice();
                    education[i] = { ...ed, degree: v };
                    patch({ education });
                  }}
                />
                <Field
                  label="Field"
                  value={ed.field}
                  onChange={(v) => {
                    const education = value.education.slice();
                    education[i] = { ...ed, field: v };
                    patch({ education });
                  }}
                />
                <Field
                  label="Institution"
                  value={ed.institution}
                  onChange={(v) => {
                    const education = value.education.slice();
                    education[i] = { ...ed, institution: v };
                    patch({ education });
                  }}
                />
                <Field
                  label="Graduation"
                  value={ed.graduation_date}
                  onChange={(v) => {
                    const education = value.education.slice();
                    education[i] = { ...ed, graduation_date: v };
                    patch({ education });
                  }}
                />
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Certifications */}
      {value.certifications.length > 0 && (
        <Section label="Certifications">
          <div className="space-y-3">
            {value.certifications.map((c, i) => (
              <div key={i} className="grid gap-2 rounded-lg border border-border/60 p-3 sm:grid-cols-3">
                <Field
                  label="Name"
                  value={c.name}
                  onChange={(v) => {
                    const certifications = value.certifications.slice();
                    certifications[i] = { ...c, name: v };
                    patch({ certifications });
                  }}
                />
                <Field
                  label="Issuer"
                  value={c.issuer}
                  onChange={(v) => {
                    const certifications = value.certifications.slice();
                    certifications[i] = { ...c, issuer: v };
                    patch({ certifications });
                  }}
                />
                <Field
                  label="Date"
                  value={c.date}
                  onChange={(v) => {
                    const certifications = value.certifications.slice();
                    certifications[i] = { ...c, date: v };
                    patch({ certifications });
                  }}
                />
              </div>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

function BulletEditor({
  bullets,
  onChange,
}: {
  bullets: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <div className="space-y-1.5">
      {bullets.map((b, j) => (
        <div key={j} className="flex items-start gap-2">
          <span aria-hidden="true" className="mt-2 text-muted-foreground">
            •
          </span>
          <Textarea
            rows={2}
            value={b}
            aria-label={`Bullet ${j + 1}`}
            className="flex-1"
            onChange={(e) => {
              const next = bullets.slice();
              next[j] = e.target.value;
              onChange(next);
            }}
          />
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-label={`Remove bullet ${j + 1}`}
            onClick={() => onChange(bullets.filter((_, k) => k !== j))}
          >
            Remove
          </Button>
        </div>
      ))}
      <Button type="button" variant="outline" size="sm" onClick={() => onChange([...bullets, ""])}>
        Add bullet
      </Button>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </label>
  );
}

// ─── Footer: counts, download menu, save-to-extension ───────────────────────────

function ResumeFooter({
  resume,
  canReset,
  onReset,
  onStartOver,
  job,
}: {
  resume: TailoredResume;
  canReset: boolean;
  onReset: () => void;
  onStartOver: () => void;
  job: Job;
}) {
  const { words, pages } = useMemo(() => countResume(resume), [resume]);
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {words} words · ~{pages} page{pages === 1 ? "" : "s"} (approx.)
        </p>
        <div className="flex items-center gap-2">
          {canReset && (
            <Button variant="ghost" size="sm" onClick={onReset}>
              Reset to AI version
            </Button>
          )}
          <Button variant="ghost" size="sm" onClick={onStartOver}>
            Start over
          </Button>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <DownloadMenu resume={resume} job={job} />
        <SaveToExtensionButton />
      </div>
    </div>
  );
}

function DownloadMenu({ resume, job }: { resume: TailoredResume; job: Job }) {
  const [open, setOpen] = useState(false);
  const [format, setFormat] = useState<"docx" | "pdf">("docx");
  const [mode, setMode] = useState<ResumeMode>("visual");
  const [downloading, setDownloading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const filename = useMemo(
    () =>
      `${job.company}-${job.title}`
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "")
        .slice(0, 80) || "tailored-resume",
    [job.company, job.title],
  );

  async function download() {
    setDownloading(true);
    setErr(null);
    try {
      const blob = await downloadResume(resume, filename, format, mode);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${filename}.${format}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setOpen(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="relative">
      <Button onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-haspopup="true">
        Download
      </Button>
      {open && (
        <div
          role="menu"
          className="absolute z-20 mt-2 w-72 space-y-3 rounded-lg border border-border bg-card p-3 shadow-md"
        >
          <Choice
            label="Format"
            options={[
              { value: "docx", label: "DOCX" },
              { value: "pdf", label: "PDF" },
            ]}
            value={format}
            onChange={(v) => setFormat(v as "docx" | "pdf")}
          />
          <Choice
            label="Style"
            options={[
              { value: "visual", label: "Visual" },
              { value: "plain", label: "Plain — max ATS" },
            ]}
            value={mode}
            onChange={(v) => setMode(v as ResumeMode)}
          />
          {err && <p className="text-xs text-destructive">{err}</p>}
          <Button className="w-full" disabled={downloading} onClick={() => void download()}>
            {downloading ? "Preparing…" : `Download .${format}`}
          </Button>
        </div>
      )}
    </div>
  );
}

function Choice({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="space-y-1">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p>
      <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-border bg-card p-0.5">
        {options.map((o) => {
          const active = value === o.value;
          return (
            <button
              key={o.value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => onChange(o.value)}
              className={
                "rounded px-3 py-1 text-xs font-medium transition-colors " +
                (active
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {o.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SaveToExtensionButton() {
  // Mirrors the login-modal "(coming soon)" pattern: looks like an action,
  // intentionally non-interactive, with a muted suffix + tooltip.
  return (
    <span
      aria-disabled="true"
      title="Save this tailored resume to your browser extension for one-click apply — coming soon"
      className="inline-flex cursor-not-allowed items-center gap-1.5 rounded-md border border-border bg-card px-4 py-2 text-sm font-medium text-muted-foreground/70"
    >
      Save to Extension
      <span className="text-xs text-muted-foreground/60">(coming soon)</span>
    </span>
  );
}

// ─── Helpers ───────────────────────────────────────────────────────────────

/** Honest, recomputed-from-content indicators. Word count is exact; the page
 * figure is an explicit approximation (the authoritative count is enforced
 * server-side at render). */
function countResume(r: TailoredResume): { words: number; pages: number } {
  const parts: string[] = [r.summary];
  for (const g of r.skills) parts.push(g.category, ...g.items);
  for (const e of r.experience) parts.push(e.title, e.company, ...e.bullets);
  for (const p of r.projects) parts.push(p.name, p.description, ...p.bullets);
  for (const ed of r.education) parts.push(ed.degree, ed.field, ed.institution);
  for (const c of r.certifications) parts.push(c.name, c.issuer);
  const words = parts.join(" ").trim().split(/\s+/).filter(Boolean).length;
  // ~600 words is a comfortable one-pager in this layout; past that we call it
  // two. Deliberately coarse so it reads as the estimate it is.
  const pages = words > 600 ? 2 : 1;
  return { words, pages };
}
