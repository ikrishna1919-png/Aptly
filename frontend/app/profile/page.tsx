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
  type ProfileAdditionalSection,
  type ProfileAffiliation,
  type ProfileCertification,
  type ProfileEducation,
  type ProfileExperience,
  type ProfileLanguage,
  type ProfileProject,
  type ProfilePublication,
  type ProfileVolunteer,
} from "@/lib/api";
import { RequireAuth } from "@/lib/auth-context";

const EMPTY_PROFILE: Profile = {
  name: "",
  headline: "",
  headline_inferred: false,
  email: "",
  phone: "",
  location: "",
  links: { linkedin: "", github: "", website: "" },
  summary: "",
  skills: [],
  experience: [],
  education: [],
  projects: [],
  achievements: [],
  certifications: [],
  languages: [],
  volunteer: [],
  publications: [],
  affiliations: [],
  additional_sections: [],
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
  field_of_study: "",
  location: "",
  graduation: "",
  gpa: "",
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

const EMPTY_CERTIFICATION: ProfileCertification = {
  name: "",
  issuer: "",
  date: "",
  credential_id: "",
};

const EMPTY_LANGUAGE: ProfileLanguage = {
  name: "",
  proficiency: "",
};

const EMPTY_VOLUNTEER: ProfileVolunteer = {
  organization: "",
  role: "",
  description: "",
  location: "",
  start_date: "",
  end_date: "",
  bullets: [],
};

const EMPTY_PUBLICATION: ProfilePublication = {
  title: "",
  venue: "",
  date: "",
  link: "",
  authors: "",
};

const EMPTY_AFFILIATION: ProfileAffiliation = {
  name: "",
  role: "",
  date: "",
};

const EMPTY_ADDITIONAL_SECTION: ProfileAdditionalSection = {
  label: "",
  content: "",
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

  function updateLink(field: "linkedin" | "github" | "website", value: string) {
    setProfile((p) => ({ ...p, links: { ...p.links, [field]: value } }));
  }

  function updateHeadline(value: string) {
    // Manually editing the headline implies the user has confirmed
    // / overridden the inferred suggestion — flip the flag so the UI
    // stops showing the "inferred" hint.
    setProfile((p) => ({ ...p, headline: value, headline_inferred: false }));
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

  function updateCertification(
    i: number,
    field: keyof ProfileCertification,
    value: string,
  ) {
    setProfile((p) => {
      const next = [...p.certifications];
      next[i] = { ...next[i], [field]: value };
      return { ...p, certifications: next };
    });
  }

  function addCertification() {
    setProfile((p) => ({
      ...p,
      certifications: [...p.certifications, { ...EMPTY_CERTIFICATION }],
    }));
  }

  function removeCertification(i: number) {
    setProfile((p) => ({
      ...p,
      certifications: p.certifications.filter((_, idx) => idx !== i),
    }));
  }

  function updateLanguage(i: number, field: keyof ProfileLanguage, value: string) {
    setProfile((p) => {
      const next = [...p.languages];
      next[i] = { ...next[i], [field]: value };
      return { ...p, languages: next };
    });
  }
  function addLanguage() {
    setProfile((p) => ({ ...p, languages: [...p.languages, { ...EMPTY_LANGUAGE }] }));
  }
  function removeLanguage(i: number) {
    setProfile((p) => ({ ...p, languages: p.languages.filter((_, idx) => idx !== i) }));
  }

  function updateVolunteer(
    i: number,
    field: keyof ProfileVolunteer,
    value: string | string[],
  ) {
    setProfile((p) => {
      const next = [...p.volunteer];
      next[i] = { ...next[i], [field]: value };
      return { ...p, volunteer: next };
    });
  }
  function addVolunteer() {
    setProfile((p) => ({ ...p, volunteer: [...p.volunteer, { ...EMPTY_VOLUNTEER }] }));
  }
  function removeVolunteer(i: number) {
    setProfile((p) => ({ ...p, volunteer: p.volunteer.filter((_, idx) => idx !== i) }));
  }

  function updatePublication(i: number, field: keyof ProfilePublication, value: string) {
    setProfile((p) => {
      const next = [...p.publications];
      next[i] = { ...next[i], [field]: value };
      return { ...p, publications: next };
    });
  }
  function addPublication() {
    setProfile((p) => ({
      ...p,
      publications: [...p.publications, { ...EMPTY_PUBLICATION }],
    }));
  }
  function removePublication(i: number) {
    setProfile((p) => ({
      ...p,
      publications: p.publications.filter((_, idx) => idx !== i),
    }));
  }

  function updateAffiliation(i: number, field: keyof ProfileAffiliation, value: string) {
    setProfile((p) => {
      const next = [...p.affiliations];
      next[i] = { ...next[i], [field]: value };
      return { ...p, affiliations: next };
    });
  }
  function addAffiliation() {
    setProfile((p) => ({
      ...p,
      affiliations: [...p.affiliations, { ...EMPTY_AFFILIATION }],
    }));
  }
  function removeAffiliation(i: number) {
    setProfile((p) => ({
      ...p,
      affiliations: p.affiliations.filter((_, idx) => idx !== i),
    }));
  }

  function updateAdditional(
    i: number,
    field: keyof ProfileAdditionalSection,
    value: string,
  ) {
    setProfile((p) => {
      const next = [...p.additional_sections];
      next[i] = { ...next[i], [field]: value };
      return { ...p, additional_sections: next };
    });
  }
  function addAdditional() {
    setProfile((p) => ({
      ...p,
      additional_sections: [...p.additional_sections, { ...EMPTY_ADDITIONAL_SECTION }],
    }));
  }
  function removeAdditional(i: number) {
    setProfile((p) => ({
      ...p,
      additional_sections: p.additional_sections.filter((_, idx) => idx !== i),
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
              <Field
                label="Headline"
                hint={
                  profile.headline_inferred
                    ? "Inferred from your most recent role — edit if needed."
                    : undefined
                }
              >
                <Input
                  value={profile.headline ?? ""}
                  onChange={(e) => updateHeadline(e.target.value)}
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
              <Field label="Website / Portfolio">
                <Input
                  value={profile.links.website ?? ""}
                  onChange={(e) => updateLink("website", e.target.value)}
                  placeholder="https://yourname.dev"
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
                        placeholder="B.S."
                      />
                    </Field>
                    <Field label="Field of study">
                      <Input
                        value={ed.field_of_study ?? ""}
                        onChange={(e) =>
                          updateEducation(i, "field_of_study", e.target.value)
                        }
                        placeholder="Computer Science"
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
                    <Field label="GPA">
                      <Input
                        value={ed.gpa ?? ""}
                        onChange={(e) => updateEducation(i, "gpa", e.target.value)}
                        placeholder="3.85/4.0"
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

            {/* Certifications — distinct from Achievements: this is
                where named credentials with an issuer live (AWS,
                PMP, CPA, Azure, etc.). The parser sorts these into
                their own bucket; the form mirrors that distinction. */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Certifications</h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addCertification}
                >
                  + Add certification
                </Button>
              </div>
              {profile.certifications.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No certifications yet.
                </p>
              )}
              {profile.certifications.map((cert, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Name" required>
                      <Input
                        required
                        value={cert.name}
                        onChange={(e) =>
                          updateCertification(i, "name", e.target.value)
                        }
                        placeholder="AWS Certified Solutions Architect"
                      />
                    </Field>
                    <Field label="Issuer">
                      <Input
                        value={cert.issuer ?? ""}
                        onChange={(e) =>
                          updateCertification(i, "issuer", e.target.value)
                        }
                        placeholder="Amazon Web Services"
                      />
                    </Field>
                    <Field label="Date">
                      <Input
                        value={cert.date ?? ""}
                        onChange={(e) =>
                          updateCertification(i, "date", e.target.value)
                        }
                        placeholder="2024 or Mar 2024"
                      />
                    </Field>
                    <Field label="Credential ID">
                      <Input
                        value={cert.credential_id ?? ""}
                        onChange={(e) =>
                          updateCertification(i, "credential_id", e.target.value)
                        }
                        placeholder="ABC-12345"
                      />
                    </Field>
                  </div>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeCertification(i)}
                    >
                      Remove certification
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Languages — spoken / written natural languages.
                Programming languages live in skills. */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Languages</h3>
                <Button type="button" variant="outline" size="sm" onClick={addLanguage}>
                  + Add language
                </Button>
              </div>
              {profile.languages.length === 0 && (
                <p className="text-sm text-muted-foreground">No languages yet.</p>
              )}
              {profile.languages.map((lang, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Language" required>
                      <Input
                        required
                        value={lang.name}
                        onChange={(e) => updateLanguage(i, "name", e.target.value)}
                        placeholder="Spanish"
                      />
                    </Field>
                    <Field label="Proficiency">
                      <Input
                        value={lang.proficiency ?? ""}
                        onChange={(e) =>
                          updateLanguage(i, "proficiency", e.target.value)
                        }
                        placeholder="Native, Fluent, B2…"
                      />
                    </Field>
                  </div>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeLanguage(i)}
                    >
                      Remove language
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Volunteer experience — kept separate from paid Experience. */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Volunteer experience</h3>
                <Button type="button" variant="outline" size="sm" onClick={addVolunteer}>
                  + Add volunteer role
                </Button>
              </div>
              {profile.volunteer.length === 0 && (
                <p className="text-sm text-muted-foreground">No volunteer roles yet.</p>
              )}
              {profile.volunteer.map((vol, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Organization" required>
                      <Input
                        required
                        value={vol.organization}
                        onChange={(e) =>
                          updateVolunteer(i, "organization", e.target.value)
                        }
                      />
                    </Field>
                    <Field label="Role">
                      <Input
                        value={vol.role ?? ""}
                        onChange={(e) => updateVolunteer(i, "role", e.target.value)}
                      />
                    </Field>
                    <Field label="Location">
                      <Input
                        value={vol.location ?? ""}
                        onChange={(e) =>
                          updateVolunteer(i, "location", e.target.value)
                        }
                      />
                    </Field>
                    <div className="grid grid-cols-2 gap-3">
                      <Field label="Start">
                        <Input
                          value={vol.start_date ?? ""}
                          onChange={(e) =>
                            updateVolunteer(i, "start_date", e.target.value)
                          }
                          placeholder="2022"
                        />
                      </Field>
                      <Field label="End">
                        <Input
                          value={vol.end_date ?? ""}
                          onChange={(e) =>
                            updateVolunteer(i, "end_date", e.target.value)
                          }
                          placeholder="2023 or Present"
                        />
                      </Field>
                    </div>
                  </div>
                  <Field label="Description">
                    <Textarea
                      rows={2}
                      value={vol.description}
                      onChange={(e) =>
                        updateVolunteer(i, "description", e.target.value)
                      }
                    />
                  </Field>
                  <Field label="Bullets (one per line)">
                    <Textarea
                      rows={Math.max(2, vol.bullets.length + 1)}
                      value={vol.bullets.join("\n")}
                      onChange={(e) =>
                        updateVolunteer(
                          i,
                          "bullets",
                          e.target.value
                            .split("\n")
                            .map((s) => s.trim())
                            .filter(Boolean),
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
                      onClick={() => removeVolunteer(i)}
                    >
                      Remove volunteer role
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Publications — papers, articles, book chapters. */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Publications</h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addPublication}
                >
                  + Add publication
                </Button>
              </div>
              {profile.publications.length === 0 && (
                <p className="text-sm text-muted-foreground">No publications yet.</p>
              )}
              {profile.publications.map((pub, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <Field label="Title" required>
                      <Input
                        required
                        value={pub.title}
                        onChange={(e) => updatePublication(i, "title", e.target.value)}
                      />
                    </Field>
                    <Field label="Venue">
                      <Input
                        value={pub.venue ?? ""}
                        onChange={(e) => updatePublication(i, "venue", e.target.value)}
                        placeholder="ICML 2023"
                      />
                    </Field>
                    <Field label="Date">
                      <Input
                        value={pub.date ?? ""}
                        onChange={(e) => updatePublication(i, "date", e.target.value)}
                        placeholder="2023"
                      />
                    </Field>
                    <Field label="Link">
                      <Input
                        value={pub.link ?? ""}
                        onChange={(e) => updatePublication(i, "link", e.target.value)}
                        placeholder="https://arxiv.org/abs/…"
                      />
                    </Field>
                  </div>
                  <Field label="Authors">
                    <Input
                      value={pub.authors ?? ""}
                      onChange={(e) =>
                        updatePublication(i, "authors", e.target.value)
                      }
                      placeholder="Jane Smith, John Doe, et al."
                    />
                  </Field>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removePublication(i)}
                    >
                      Remove publication
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Professional affiliations / memberships. */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Professional affiliations</h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addAffiliation}
                >
                  + Add affiliation
                </Button>
              </div>
              {profile.affiliations.length === 0 && (
                <p className="text-sm text-muted-foreground">No affiliations yet.</p>
              )}
              {profile.affiliations.map((aff, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <div className="grid gap-3 sm:grid-cols-3">
                    <Field label="Organization" required>
                      <Input
                        required
                        value={aff.name}
                        onChange={(e) => updateAffiliation(i, "name", e.target.value)}
                        placeholder="IEEE, ACM, …"
                      />
                    </Field>
                    <Field label="Role">
                      <Input
                        value={aff.role ?? ""}
                        onChange={(e) => updateAffiliation(i, "role", e.target.value)}
                        placeholder="Member"
                      />
                    </Field>
                    <Field label="Date">
                      <Input
                        value={aff.date ?? ""}
                        onChange={(e) => updateAffiliation(i, "date", e.target.value)}
                        placeholder="2020 – Present"
                      />
                    </Field>
                  </div>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeAffiliation(i)}
                    >
                      Remove affiliation
                    </Button>
                  </div>
                </Card>
              ))}
            </div>

            <Separator />

            {/* Additional / custom sections — catch-all for content the
                parser couldn't bucket (Hobbies, Patents, Conference Talks
                etc.). Free-form label + body. */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-base font-semibold">Additional sections</h3>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={addAdditional}
                >
                  + Add section
                </Button>
              </div>
              {profile.additional_sections.length === 0 && (
                <p className="text-sm text-muted-foreground">
                  No custom sections yet — use this for anything the standard
                  buckets above don&apos;t cover (Hobbies, Patents, Conference
                  Talks, etc.).
                </p>
              )}
              {profile.additional_sections.map((sec, i) => (
                <Card key={i} className="space-y-3 p-4">
                  <Field label="Section heading" required>
                    <Input
                      required
                      value={sec.label}
                      onChange={(e) => updateAdditional(i, "label", e.target.value)}
                      placeholder="Hobbies, Patents, …"
                    />
                  </Field>
                  <Field label="Content">
                    <Textarea
                      rows={3}
                      value={sec.content}
                      onChange={(e) =>
                        updateAdditional(i, "content", e.target.value)
                      }
                    />
                  </Field>
                  <div className="flex justify-end">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={() => removeAdditional(i)}
                    >
                      Remove section
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
  hint,
  children,
}: {
  label: string;
  required?: boolean;
  /** Optional helper text rendered under the label, before the
   * input. Used e.g. to flag an inferred headline so the user
   * knows to confirm or edit it. */
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block space-y-1.5">
      <span className="text-sm font-medium">
        {label}
        {required && <span className="ml-0.5 text-primary">*</span>}
      </span>
      {hint && (
        <span className="block text-xs text-muted-foreground">{hint}</span>
      )}
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
    headline_inferred: p.headline_inferred ?? false,
    email: p.email ?? "",
    phone: p.phone ?? "",
    location: p.location ?? "",
    links: {
      linkedin: p.links?.linkedin ?? "",
      github: p.links?.github ?? "",
      website: p.links?.website ?? "",
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
      field_of_study: e.field_of_study ?? "",
      location: e.location ?? "",
      graduation: e.graduation ?? "",
      gpa: e.gpa ?? "",
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
    certifications: (p.certifications ?? []).map((c) => ({
      name: c.name ?? "",
      issuer: c.issuer ?? "",
      date: c.date ?? "",
      credential_id: c.credential_id ?? "",
    })),
    languages: (p.languages ?? []).map((l) => ({
      name: l.name ?? "",
      proficiency: l.proficiency ?? "",
    })),
    volunteer: (p.volunteer ?? []).map((v) => ({
      organization: v.organization ?? "",
      role: v.role ?? "",
      description: v.description ?? "",
      location: v.location ?? "",
      start_date: v.start_date ?? "",
      end_date: v.end_date ?? "",
      bullets: v.bullets ?? [],
    })),
    publications: (p.publications ?? []).map((pb) => ({
      title: pb.title ?? "",
      venue: pb.venue ?? "",
      date: pb.date ?? "",
      link: pb.link ?? "",
      authors: pb.authors ?? "",
    })),
    affiliations: (p.affiliations ?? []).map((af) => ({
      name: af.name ?? "",
      role: af.role ?? "",
      date: af.date ?? "",
    })),
    additional_sections: (p.additional_sections ?? []).map((s) => ({
      label: s.label ?? "",
      content: s.content ?? "",
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
      website: opt(p.links.website),
    },
    education: p.education.map((ed) => ({
      ...ed,
      field_of_study: opt(ed.field_of_study),
      location: opt(ed.location),
      gpa: opt(ed.gpa),
    })),
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
    certifications: p.certifications.map((c) => ({
      ...c,
      issuer: opt(c.issuer),
      date: opt(c.date),
      credential_id: opt(c.credential_id),
    })),
    languages: p.languages
      .filter((l) => (l.name ?? "").trim())
      .map((l) => ({
        ...l,
        proficiency: opt(l.proficiency),
      })),
    volunteer: p.volunteer
      .filter((v) => (v.organization ?? "").trim())
      .map((v) => ({
        ...v,
        role: opt(v.role),
        location: opt(v.location),
        start_date: opt(v.start_date),
        end_date: opt(v.end_date),
      })),
    publications: p.publications
      .filter((pb) => (pb.title ?? "").trim())
      .map((pb) => ({
        ...pb,
        venue: opt(pb.venue),
        date: opt(pb.date),
        link: opt(pb.link),
        authors: opt(pb.authors),
      })),
    affiliations: p.affiliations
      .filter((af) => (af.name ?? "").trim())
      .map((af) => ({
        ...af,
        role: opt(af.role),
        date: opt(af.date),
      })),
    additional_sections: p.additional_sections.filter(
      (s) => (s.label ?? "").trim() || (s.content ?? "").trim(),
    ),
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
