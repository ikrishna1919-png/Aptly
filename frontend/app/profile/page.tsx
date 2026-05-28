"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

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
import { Textarea } from "@/components/ui/textarea";
import {
  PARSE_STILL_WORKING_AFTER_MS,
  ParseEmptyResultError,
  ParseTimeoutError,
  RESUME_UPLOAD_ACCEPT,
  RESUME_UPLOAD_MAX_BYTES,
  fetchProfile,
  parseProfileFile,
  parseProfileText,
  saveProfile,
  type ParseRunStatus,
  type Profile,
  type ProfileAchievement,
  type ProfileEducation,
  type ProfileExperience,
  type ProfileProject,
} from "@/lib/api";
import { RequireAuth } from "@/lib/auth-context";

const EMPTY_PROFILE: Profile = {
  name: "",
  headline: "",
  email: "",
  phone: "",
  location: "",
  links: { linkedin: "", github: "" },
  summary: "",
  skills: [],
  experience: [],
  education: [],
  projects: [],
  achievements: [],
  section_order: [],
};

const EMPTY_EXPERIENCE: ProfileExperience = {
  company: "",
  title: "",
  location: "",
  start: "",
  end: "",
  bullets: [],
};

const EMPTY_EDUCATION: ProfileEducation = {
  school: "",
  degree: "",
  location: "",
  graduation: "",
};

const EMPTY_PROJECT: ProfileProject = {
  name: "",
  description: "",
  technologies: [],
  link: "",
  start_date: "",
  end_date: "",
};

const EMPTY_ACHIEVEMENT: ProfileAchievement = {
  title: "",
  description: "",
  date: "",
};

export default function ProfilePage() {
  // The page itself is one big controlled form; gate the whole
  // tree behind RequireAuth so the in-flight fetch doesn't 401
  // before the redirect lands.
  return (
    <RequireAuth>
      <ProfileEditor />
    </RequireAuth>
  );
}

function ProfileEditor() {
  const [profile, setProfile] = useState<Profile>(EMPTY_PROFILE);
  const [pasted, setPasted] = useState("");
  const [loading, setLoading] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  // Abort handle so the spinner can be cancelled (e.g. on retry or
  // navigation) without leaving the polling loop running in the
  // background.
  const parseAbortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchProfile();
      setProfile(normaliseProfile(data));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load profile");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function onParse() {
    if (!pasted.trim()) return setError("Paste your resume text first");
    await runParse("Parsing your resume…", (signal, onProgress) =>
      parseProfileText(pasted, { signal, onProgress }),
    );
  }

  async function onUpload(file: File) {
    if (!file) return;
    if (file.size > RESUME_UPLOAD_MAX_BYTES) {
      setError(
        `File is too large. Maximum size is ${
          RESUME_UPLOAD_MAX_BYTES / (1024 * 1024)
        } MB.`,
      );
      return;
    }
    await runParse(`Parsing ${file.name}…`, (signal, onProgress) =>
      parseProfileFile(file, { signal, onProgress }),
    );
  }

  function getParseSignal(): AbortSignal {
    parseAbortRef.current?.abort();
    const controller = new AbortController();
    parseAbortRef.current = controller;
    return controller.signal;
  }

  async function runParse(
    initialMessage: string,
    call: (
      signal: AbortSignal,
      onProgress: (status: ParseRunStatus) => void,
    ) => Promise<Profile>,
  ) {
    const signal = getParseSignal();
    setError(null);
    setInfo(initialMessage);
    setParsing(true);

    // Swap the in-flight message to a calmer "still working…" after
    // a few polls. Backed by the same constant the polling loop
    // uses so the UI matches the worst-case wait the API has
    // promised. The timer is cancelled in `finally` so a fast parse
    // never flashes the calmer copy.
    const stillWorkingTimer = setTimeout(() => {
      setInfo(
        "Still working — the AI structural pass can take up to a minute on a long resume.",
      );
    }, PARSE_STILL_WORKING_AFTER_MS);

    try {
      const parsed = await call(signal, (_status) => {
        // Polling-progress callback fires on each poll. We only use
        // it to keep the spinner alive — the still-working swap is
        // time-based above.
      });
      setProfile(normaliseProfile(parsed));
      setInfo("Parsed — review and edit, then click Save.");
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        setInfo(null);
      } else if (e instanceof ParseEmptyResultError) {
        setInfo(e.message);
      } else if (e instanceof ParseTimeoutError) {
        setError(e.message);
      } else {
        // The polling loop throws `new Error(run.error)` when the
        // backend writes status=failed, so `e.message` here carries
        // the REAL backend error (e.g. "Parse failed — RuntimeError:
        // bad magic bytes"). Show it verbatim instead of a generic
        // "took too long" copy.
        setError(e instanceof Error ? e.message : "Parse failed");
      }
    } finally {
      clearTimeout(stillWorkingTimer);
      setParsing(false);
    }
  }

  // Stop polling if the page unmounts mid-parse.
  useEffect(() => {
    return () => parseAbortRef.current?.abort();
  }, []);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    setSaving(true);
    try {
      const saved = await saveProfile(cleanForSave(profile));
      setProfile(normaliseProfile(saved));
      setInfo("Saved. The tailor flow will use this profile from now on.");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  function updateRoot<K extends keyof Profile>(key: K, value: Profile[K]) {
    setProfile((p) => ({ ...p, [key]: value }));
  }

  function updateLink(field: "linkedin" | "github", value: string) {
    setProfile((p) => ({ ...p, links: { ...p.links, [field]: value } }));
  }

  function updateExperience(
    i: number,
    field: keyof ProfileExperience,
    value: string | string[],
  ) {
    setProfile((p) => {
      const next = [...p.experience];
      next[i] = { ...next[i], [field]: value };
      return { ...p, experience: next };
    });
  }

  function updateEducation(
    i: number,
    field: keyof ProfileEducation,
    value: string,
  ) {
    setProfile((p) => {
      const next = [...p.education];
      next[i] = { ...next[i], [field]: value };
      return { ...p, education: next };
    });
  }

  function addExperience() {
    setProfile((p) => ({ ...p, experience: [...p.experience, { ...EMPTY_EXPERIENCE }] }));
  }

  function removeExperience(i: number) {
    setProfile((p) => ({
      ...p,
      experience: p.experience.filter((_, idx) => idx !== i),
    }));
  }

  function addEducation() {
    setProfile((p) => ({ ...p, education: [...p.education, { ...EMPTY_EDUCATION }] }));
  }

  function removeEducation(i: number) {
    setProfile((p) => ({
      ...p,
      education: p.education.filter((_, idx) => idx !== i),
    }));
  }

  function updateProject(
    i: number,
    field: keyof ProfileProject,
    value: string | string[],
  ) {
    setProfile((p) => {
      const next = [...p.projects];
      next[i] = { ...next[i], [field]: value };
      return { ...p, projects: next };
    });
  }

  function addProject() {
    setProfile((p) => ({ ...p, projects: [...p.projects, { ...EMPTY_PROJECT }] }));
  }

  function removeProject(i: number) {
    setProfile((p) => ({
      ...p,
      projects: p.projects.filter((_, idx) => idx !== i),
    }));
  }

  function updateAchievement(
    i: number,
    field: keyof ProfileAchievement,
    value: string,
  ) {
    setProfile((p) => {
      const next = [...p.achievements];
      next[i] = { ...next[i], [field]: value };
      return { ...p, achievements: next };
    });
  }

  function addAchievement() {
    setProfile((p) => ({
      ...p,
      achievements: [...p.achievements, { ...EMPTY_ACHIEVEMENT }],
    }));
  }

  function removeAchievement(i: number) {
    setProfile((p) => ({
      ...p,
      achievements: p.achievements.filter((_, idx) => idx !== i),
    }));
  }

  return (
    <div className="container max-w-3xl space-y-8 py-10">
      <header className="space-y-3">
        <div className="flex items-center gap-2">
          <Badge variant="default">Profile</Badge>
          <Link
            href="/admin"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            admin →
          </Link>
        </div>
        <h1 className="text-3xl font-semibold tracking-tight">Your candidate profile</h1>
        <p className="max-w-xl text-sm text-muted-foreground">
          This is the profile the tailoring flow runs against. Paste your
          resume below to autofill, then review and save. The model only
          extracts what&apos;s in the source — it never invents.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Autofill from your resume</CardTitle>
          <CardDescription>
            Upload a PDF or DOCX, or paste the text. The hybrid parser
            pulls contact info, work history, education, skills,
            projects, and achievements. The result populates the form
            below for review; nothing saves until you click Save.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <ResumeDropzone
            onPick={(file) => void onUpload(file)}
            disabled={parsing}
          />
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="h-px flex-1 bg-border" />
            <span>or paste the text</span>
            <span className="h-px flex-1 bg-border" />
          </div>
          <Textarea
            rows={8}
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            placeholder="Paste your resume as plain text."
          />
          <div className="flex flex-wrap items-center justify-end gap-3">
            {parsing && (
              <span
                className="inline-flex items-center gap-2 text-xs text-muted-foreground"
                role="status"
                aria-live="polite"
              >
                <Spinner /> Parsing your resume…
              </span>
            )}
            <Button
              type="button"
              onClick={() => void onParse()}
              disabled={parsing || !pasted.trim()}
            >
              {parsing ? "Parsing…" : error ? "Retry parse" : "Parse pasted text"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <form onSubmit={onSave}>
        <Card>
          <CardHeader>
            <CardTitle>Profile</CardTitle>
            <CardDescription>
              Edit anything. Required: name. Everything else is optional.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* Basic info */}
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" required>
                <Input
                  required
                  value={profile.name}
                  onChange={(e) => updateRoot("name", e.target.value)}
                />
              </Field>
              <Field label="Headline">
                <Input
                  value={profile.headline ?? ""}
                  onChange={(e) => updateRoot("headline", e.target.value)}
                  placeholder="Senior Software Engineer"
                />
              </Field>
              <Field label="Email">
                <Input
                  type="email"
                  value={profile.email ?? ""}
                  onChange={(e) => updateRoot("email", e.target.value)}
                />
              </Field>
              <Field label="Phone">
                <Input
                  value={profile.phone ?? ""}
                  onChange={(e) => updateRoot("phone", e.target.value)}
                />
              </Field>
              <Field label="Location">
                <Input
                  value={profile.location ?? ""}
                  onChange={(e) => updateRoot("location", e.target.value)}
                />
              </Field>
              <Field label="LinkedIn">
                <Input
                  value={profile.links.linkedin ?? ""}
                  onChange={(e) => updateLink("linkedin", e.target.value)}
                  placeholder="linkedin.com/in/…"
                />
              </Field>
              <Field label="GitHub">
                <Input
                  value={profile.links.github ?? ""}
                  onChange={(e) => updateLink("github", e.target.value)}
                  placeholder="github.com/…"
                />
              </Field>
            </div>

            <Field label="Summary">
              <Textarea
                rows={4}
                value={profile.summary}
                onChange={(e) => updateRoot("summary", e.target.value)}
              />
            </Field>

            <Field label="Skills (comma-separated)">
              <Input
                value={profile.skills.join(", ")}
                onChange={(e) =>
                  updateRoot(
                    "skills",
                    e.target.value
                      .split(",")
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )
                }
                placeholder="Python, FastAPI, React, AWS"
              />
            </Field>

            <Separator />

            {/* Experience */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Experience</h3>
                <Button type="button" variant="outline" size="sm" onClick={addExperience}>
                  + Add role
                </Button>
              </div>
              {profile.experience.length === 0 && (
                <p className="text-sm text-muted-foreground">No roles yet.</p>
              )}
              {profile.experience.map((exp, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Title" required>
                      <Input
                        required
                        value={exp.title}
                        onChange={(e) => updateExperience(i, "title", e.target.value)}
                      />
                    </Field>
                    <Field label="Company" required>
                      <Input
                        required
                        value={exp.company}
                        onChange={(e) => updateExperience(i, "company", e.target.value)}
                      />
                    </Field>
                    <Field label="Location">
                      <Input
                        value={exp.location ?? ""}
                        onChange={(e) => updateExperience(i, "location", e.target.value)}
                      />
                    </Field>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Start">
                        <Input
                          value={exp.start}
                          onChange={(e) => updateExperience(i, "start", e.target.value)}
                          placeholder="2023-02"
                        />
                      </Field>
                      <Field label="End">
                        <Input
                          value={exp.end}
                          onChange={(e) => updateExperience(i, "end", e.target.value)}
                          placeholder="Present"
                        />
                      </Field>
                    </div>
                  </div>
                  <Field label="Bullets (one per line)">
                    <Textarea
                      rows={Math.max(3, exp.bullets.length + 1)}
                      value={exp.bullets.join("\n")}
                      onChange={(e) =>
                        updateExperience(
                          i,
                          "bullets",
                          e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                        )
                      }
                    />
                  </Field>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeExperience(i)}
                    >
                      Remove role
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Education */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Education</h3>
                <Button type="button" variant="outline" size="sm" onClick={addEducation}>
                  + Add entry
                </Button>
              </div>
              {profile.education.length === 0 && (
                <p className="text-sm text-muted-foreground">No entries yet.</p>
              )}
              {profile.education.map((ed, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="School" required>
                      <Input
                        required
                        value={ed.school}
                        onChange={(e) => updateEducation(i, "school", e.target.value)}
                      />
                    </Field>
                    <Field label="Degree" required>
                      <Input
                        required
                        value={ed.degree}
                        onChange={(e) => updateEducation(i, "degree", e.target.value)}
                      />
                    </Field>
                    <Field label="Location">
                      <Input
                        value={ed.location ?? ""}
                        onChange={(e) => updateEducation(i, "location", e.target.value)}
                      />
                    </Field>
                    <Field label="Graduation year">
                      <Input
                        value={ed.graduation}
                        onChange={(e) =>
                          updateEducation(i, "graduation", e.target.value)
                        }
                        placeholder="2018"
                      />
                    </Field>
                  </div>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeEducation(i)}
                    >
                      Remove entry
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Projects */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Projects</h3>
                <Button type="button" variant="outline" size="sm" onClick={addProject}>
                  + Add project
                </Button>
              </div>
              {profile.projects.length === 0 && (
                <p className="text-sm text-muted-foreground">No projects yet.</p>
              )}
              {profile.projects.map((proj, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Name" required>
                      <Input
                        required
                        value={proj.name}
                        onChange={(e) => updateProject(i, "name", e.target.value)}
                      />
                    </Field>
                    <Field label="Link">
                      <Input
                        value={proj.link ?? ""}
                        onChange={(e) => updateProject(i, "link", e.target.value)}
                        placeholder="https://github.com/…"
                      />
                    </Field>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Start">
                        <Input
                          value={proj.start_date ?? ""}
                          onChange={(e) =>
                            updateProject(i, "start_date", e.target.value)
                          }
                          placeholder="2023-02"
                        />
                      </Field>
                      <Field label="End">
                        <Input
                          value={proj.end_date ?? ""}
                          onChange={(e) =>
                            updateProject(i, "end_date", e.target.value)
                          }
                          placeholder="2023-06 or Present"
                        />
                      </Field>
                    </div>
                  </div>
                  <Field label="Description">
                    <Textarea
                      rows={2}
                      value={proj.description}
                      onChange={(e) => updateProject(i, "description", e.target.value)}
                    />
                  </Field>
                  <Field label="Technologies (comma-separated)">
                    <Input
                      value={proj.technologies.join(", ")}
                      onChange={(e) =>
                        updateProject(
                          i,
                          "technologies",
                          e.target.value
                            .split(",")
                            .map((s) => s.trim())
                            .filter(Boolean),
                        )
                      }
                      placeholder="TypeScript, Postgres, AWS"
                    />
                  </Field>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeProject(i)}
                    >
                      Remove project
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Achievements */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Achievements</h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addAchievement}
                >
                  + Add achievement
                </Button>
              </div>
              {profile.achievements.length === 0 && (
                <p className="text-sm text-muted-foreground">No achievements yet.</p>
              )}
              {profile.achievements.map((ach, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Title" required>
                      <Input
                        required
                        value={ach.title}
                        onChange={(e) => updateAchievement(i, "title", e.target.value)}
                      />
                    </Field>
                    <Field label="Date">
                      <Input
                        value={ach.date ?? ""}
                        onChange={(e) => updateAchievement(i, "date", e.target.value)}
                        placeholder="2023 or Mar 2023"
                      />
                    </Field>
                  </div>
                  <Field label="Description">
                    <Textarea
                      rows={2}
                      value={ach.description}
                      onChange={(e) =>
                        updateAchievement(i, "description", e.target.value)
                      }
                    />
                  </Field>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeAchievement(i)}
                    >
                      Remove achievement
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm">
                {loading && <span className="text-muted-foreground">Loading…</span>}
                {info && <span className="text-muted-foreground">{info}</span>}
                {error && <span className="text-destructive">{error}</span>}
              </div>
              <Button type="submit" disabled={saving}>
                {saving ? "Saving…" : "Save profile"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  );
}

function ResumeDropzone({
  onPick,
  disabled,
}: {
  onPick: (file: File) => void;
  disabled: boolean;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragging, setDragging] = useState(false);

  function handleFiles(files: FileList | null) {
    const file = files?.[0];
    if (!file) return;
    onPick(file);
  }

  return (
    <div
      className={`flex flex-col items-center justify-center gap-1 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors ${
        dragging
          ? "border-primary bg-primary/5"
          : "border-border bg-background hover:bg-secondary/40"
      } ${disabled ? "pointer-events-none opacity-60" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragging(false);
        if (disabled) return;
        handleFiles(e.dataTransfer.files);
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept={RESUME_UPLOAD_ACCEPT}
        className="hidden"
        onChange={(e) => {
          handleFiles(e.target.files);
          // Allow re-selecting the same file after a parse — input
          // value is sticky until we clear it.
          e.target.value = "";
        }}
      />
      <p className="text-sm font-medium text-foreground">
        Drop a PDF or DOCX here
      </p>
      <p className="text-xs text-muted-foreground">
        or{" "}
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded-sm"
        >
          choose a file
        </button>
        {" — up to "}
        {RESUME_UPLOAD_MAX_BYTES / (1024 * 1024)} MB
      </p>
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">
        {label}
        {required && <span className="ml-0.5 text-primary">*</span>}
      </span>
      {children}
    </label>
  );
}

/** Backend may omit optional fields; coerce to the form's all-fields shape so
 * controlled inputs never receive `undefined`. */
function normaliseProfile(p: Profile): Profile {
  return {
    name: p.name ?? "",
    headline: p.headline ?? "",
    email: p.email ?? "",
    phone: p.phone ?? "",
    location: p.location ?? "",
    links: {
      linkedin: p.links?.linkedin ?? "",
      github: p.links?.github ?? "",
    },
    summary: p.summary ?? "",
    skills: p.skills ?? [],
    experience: (p.experience ?? []).map((e) => ({
      company: e.company ?? "",
      title: e.title ?? "",
      location: e.location ?? "",
      start: e.start ?? "",
      end: e.end ?? "",
      bullets: e.bullets ?? [],
    })),
    education: (p.education ?? []).map((e) => ({
      school: e.school ?? "",
      degree: e.degree ?? "",
      location: e.location ?? "",
      graduation: e.graduation ?? "",
    })),
    projects: (p.projects ?? []).map((pr) => ({
      name: pr.name ?? "",
      description: pr.description ?? "",
      technologies: pr.technologies ?? [],
      link: pr.link ?? "",
      start_date: pr.start_date ?? "",
      end_date: pr.end_date ?? "",
    })),
    achievements: (p.achievements ?? []).map((a) => ({
      title: a.title ?? "",
      description: a.description ?? "",
      date: a.date ?? "",
    })),
    section_order: p.section_order ?? [],
  };
}

/** Drop empty optional strings before sending — keeps the saved JSON tidy
 * (and matches what the backend would compute for the candidate fingerprint
 * if the field had been omitted entirely). */
function cleanForSave(p: Profile): Profile {
  const opt = (v: string | null | undefined): string | null =>
    v && v.trim() ? v.trim() : null;
  return {
    ...p,
    headline: opt(p.headline),
    email: opt(p.email),
    phone: opt(p.phone),
    location: opt(p.location),
    links: {
      linkedin: opt(p.links.linkedin),
      github: opt(p.links.github),
    },
    projects: p.projects.map((pr) => ({
      ...pr,
      link: opt(pr.link),
      start_date: opt(pr.start_date),
      end_date: opt(pr.end_date),
    })),
    achievements: p.achievements.map((a) => ({
      ...a,
      date: opt(a.date),
    })),
  };
}

/** Tiny inline spinner so the parsing indicator reads as alive. The
 * 90-second worst case means a static label could plausibly look
 * frozen — the rotating ring makes the in-flight state unambiguous. */
function Spinner() {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      width="12"
      height="12"
      className="animate-spin"
      fill="none"
    >
      <circle
        cx="12"
        cy="12"
        r="9"
        stroke="currentColor"
        strokeWidth="2.5"
        opacity="0.25"
      />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}
