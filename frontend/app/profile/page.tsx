"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

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
  type ProfileSkillGroup,
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
  // Default to flat shape so the comma-separated editor is the
  // initial UX; `SkillsEditor`'s "Convert to categorised" button
  // promotes to the grouped shape when the user wants labels.
  skills: [] as string[],
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
  start: "",
  end: "",
  graduation: "",
  gpa: "",
  coursework: [],
};

const EMPTY_PROJECT: ProfileProject = {
  name: "",
  description: "",
  bullets: [],
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
  const [showResumeTools, setShowResumeTools] = useState(false);
  const [loading, setLoading] = useState(false);
  const [parsing, setParsing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  // After "+ Add …" fires we set this to the new entry's focus
  // key (e.g. `experience-0`); the matching `<input
  // data-focus-target=…>` then scrolls into view + receives
  // focus via the effect below. This is what makes "add" feel
  // like it happened where the user clicked — without it the
  // freshly-created entry appears wherever the section lives
  // on the page, sometimes off-screen on a long form.
  const [focusKey, setFocusKey] = useState<string | null>(null);
  // Abort handle so the spinner can be cancelled (e.g. on retry or
  // navigation) without leaving the polling loop running in the
  // background.
  const parseAbortRef = useRef<AbortController | null>(null);

  // After the DOM updates with the new entry, scroll it into view
  // and focus its first input so the user lands inside the row
  // they just created.
  useEffect(() => {
    if (!focusKey) return;
    // Wait one frame for the new node to mount.
    const id = requestAnimationFrame(() => {
      const el = document.querySelector<HTMLElement>(
        `[data-focus-target="${focusKey}"]`,
      );
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        // Focus AFTER the smooth scroll has had a chance to start
        // — focus on a freshly-scrolled element can re-jank the
        // viewport on some browsers when called too eagerly.
        setTimeout(() => el.focus({ preventScroll: true }), 50);
      }
      setFocusKey(null);
    });
    return () => cancelAnimationFrame(id);
  }, [focusKey]);

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
        // the REAL backend error.
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

  // Required-field gate. A profile needs a name AND at least one
  // experience OR education entry before the tailoring flow has
  // anything to work with. Optional fields stay optional. Computed
  // here so the save button + the hint message stay in sync.
  const validation = useMemo<{ ok: boolean; message: string | null }>(() => {
    if (!profile.name.trim()) {
      return { ok: false, message: "Add your name to save." };
    }
    const hasHistory =
      profile.experience.some((e) => e.company.trim() || e.title.trim()) ||
      profile.education.some((e) => e.school.trim() || e.degree.trim());
    if (!hasHistory) {
      return {
        ok: false,
        message: "Add at least one experience or education entry to save.",
      };
    }
    return { ok: true, message: null };
  }, [profile.name, profile.experience, profile.education]);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (!validation.ok) {
      setError(validation.message);
      return;
    }
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
    value: string | string[],
  ) {
    setProfile((p) => {
      const next = [...p.education];
      next[i] = { ...next[i], [field]: value };
      return { ...p, education: next };
    });
  }

  function addExperience() {
    setProfile((p) => ({ ...p, experience: [{ ...EMPTY_EXPERIENCE }, ...p.experience] }));
    setFocusKey("experience-0");
  }

  function removeExperience(i: number) {
    setProfile((p) => ({
      ...p,
      experience: p.experience.filter((_, idx) => idx !== i),
    }));
  }

  function addEducation() {
    setProfile((p) => ({ ...p, education: [{ ...EMPTY_EDUCATION }, ...p.education] }));
    setFocusKey("education-0");
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
    setProfile((p) => ({ ...p, projects: [{ ...EMPTY_PROJECT }, ...p.projects] }));
    setFocusKey("project-0");
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
      achievements: [{ ...EMPTY_ACHIEVEMENT }, ...p.achievements],
    }));
    setFocusKey("achievement-0");
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
      certifications: [{ ...EMPTY_CERTIFICATION }, ...p.certifications],
    }));
    setFocusKey("certification-0");
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
    setProfile((p) => ({ ...p, languages: [{ ...EMPTY_LANGUAGE }, ...p.languages] }));
    setFocusKey("language-0");
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
    setProfile((p) => ({ ...p, volunteer: [{ ...EMPTY_VOLUNTEER }, ...p.volunteer] }));
    setFocusKey("volunteer-0");
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
      publications: [{ ...EMPTY_PUBLICATION }, ...p.publications],
    }));
    setFocusKey("publication-0");
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
      affiliations: [{ ...EMPTY_AFFILIATION }, ...p.affiliations],
    }));
    setFocusKey("affiliation-0");
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
      additional_sections: [{ ...EMPTY_ADDITIONAL_SECTION }, ...p.additional_sections],
    }));
    setFocusKey("additional-0");
  }
  function removeAdditional(i: number) {
    setProfile((p) => ({
      ...p,
      additional_sections: p.additional_sections.filter((_, idx) => idx !== i),
    }));
  }

  return (
    <div className="bg-secondary/30">
      <div className="container max-w-6xl space-y-10 py-10">
        {/* Header strip — keeps the same Aptly accent + serif voice
            the landing page uses, so the in-app surface doesn't feel
            like a different product. */}
        <header className="space-y-3">
          <div className="flex items-center gap-2">
            <Badge variant="default">Profile</Badge>
            <Link
              href="/jobs"
              className="text-sm text-muted-foreground hover:text-foreground"
            >
              jobs →
            </Link>
          </div>
          <h1 className="font-display text-3xl font-medium tracking-tight sm:text-4xl">
            Your candidate profile
          </h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            The source of truth for resume tailoring. Aptly only uses what you put
            here — no scraping, no auto-enrichment, no inferred history. Update
            anything, save, and the tailoring flow picks the new shape up
            instantly.
          </p>
        </header>

        <form onSubmit={onSave} className="space-y-8">
          {/* ── Three-column header ──
              Left: live identity preview card (avatar + name + headline
              + key contact at a glance).
              Middle: identity form (the editable side of the same data
              the left card displays).
              Right: job-relevant links only — LinkedIn, GitHub,
              Portfolio. */}
          <div className="grid gap-6 lg:grid-cols-12">
            <div className="lg:col-span-4">
              <IdentityCard profile={profile} />
            </div>

            <div className="lg:col-span-5">
              <Card className="h-full border-border/70 shadow-sm">
                <CardHeader className="space-y-1">
                  <CardTitle className="font-display text-xl font-medium tracking-tight">
                    Identity
                  </CardTitle>
                  <CardDescription>
                    The basics. Required: name. Everything else is optional.
                  </CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <Field label="Full name" required>
                    <Input
                      required
                      value={profile.name}
                      onChange={(e) => updateRoot("name", e.target.value)}
                      placeholder="Jane Smith"
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
                  <div className="grid gap-4 sm:grid-cols-2">
                    <Field label="Email">
                      <Input
                        type="email"
                        value={profile.email ?? ""}
                        onChange={(e) => updateRoot("email", e.target.value)}
                        placeholder="you@example.com"
                      />
                    </Field>
                    <Field label="Phone">
                      <Input
                        value={profile.phone ?? ""}
                        onChange={(e) => updateRoot("phone", e.target.value)}
                        placeholder="+1 555 123 4567"
                      />
                    </Field>
                  </div>
                  <Field label="Location">
                    <Input
                      value={profile.location ?? ""}
                      onChange={(e) => updateRoot("location", e.target.value)}
                      placeholder="City, State / Country"
                    />
                  </Field>
                </CardContent>
              </Card>
            </div>

            <div className="lg:col-span-3">
              <Card className="h-full border-border/70 shadow-sm">
                <CardHeader className="space-y-1">
                  <CardTitle className="font-display text-xl font-medium tracking-tight">
                    Links
                  </CardTitle>
                  <CardDescription>Job-relevant only.</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
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
                  <Field label="Portfolio / Website">
                    <Input
                      value={profile.links.website ?? ""}
                      onChange={(e) => updateLink("website", e.target.value)}
                      placeholder="https://yourname.dev"
                    />
                  </Field>
                </CardContent>
              </Card>
            </div>
          </div>

          {/* ── Optional resume-import toggle ──
              Collapsed by default so the page reads "edit your profile,"
              not "upload a resume." Power users (or first-time setup)
              click to expand. Parsing is still the full AI pipeline;
              once parsed the form below populates and the toggle stays
              open so the result is reviewable in context. */}
          <Card className="border-dashed border-border/70 shadow-none">
            <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
              <div className="space-y-1">
                <CardTitle className="font-display text-lg font-medium tracking-tight">
                  Start from a resume
                </CardTitle>
                <CardDescription>
                  Optional — drop a PDF or DOCX and we&apos;ll pre-fill the
                  sections below. Nothing saves until you click Save.
                </CardDescription>
              </div>
              <Button
                type="button"
                variant={showResumeTools ? "ghost" : "outline"}
                size="sm"
                onClick={() => setShowResumeTools((s) => !s)}
              >
                {showResumeTools ? "Hide" : "Import from resume"}
              </Button>
            </CardHeader>
            {showResumeTools && (
              <CardContent className="space-y-4">
                <ResumeDropzone onPick={(file) => void onUpload(file)} disabled={parsing} />
                <div className="flex items-center gap-3 text-xs text-muted-foreground">
                  <span className="h-px flex-1 bg-border" />
                  <span>or paste the text</span>
                  <span className="h-px flex-1 bg-border" />
                </div>
                <Textarea
                  rows={6}
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
            )}
          </Card>

          {/* ── Full-width résumé sections ──
              These have too much content for the 3-col header strip, so
              they stack full-width under it. Each section is its own
              card with consistent rhythm: title + add button + entries.
              Add prepends + scrolls/focuses (see `setFocusKey`). */}

          <SectionShell
            title="Summary"
            description="A short professional snapshot — two or three sentences."
          >
            <Textarea
              rows={4}
              value={profile.summary}
              onChange={(e) => updateRoot("summary", e.target.value)}
              placeholder="Senior backend engineer with seven years of experience…"
            />
          </SectionShell>

          <SectionShell
            title="Skills"
            description="A flat list works for most. Use categories when your resume groups them."
          >
            <SkillsEditor
              skills={profile.skills}
              onChange={(next) => updateRoot("skills", next)}
            />
          </SectionShell>

          <SectionShell
            title="Experience"
            description="Paid roles, most recent first. Multiple roles at one employer get one entry each."
            addLabel="+ Add role"
            onAdd={addExperience}
            emptyText="No roles yet. Click + Add role to start."
            isEmpty={profile.experience.length === 0}
          >
            {profile.experience.map((exp, i) => (
              <EntryCard key={i} onRemove={() => removeExperience(i)} removeLabel="Remove role">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Title" required>
                    <Input
                      required
                      value={exp.title}
                      data-focus-target={`experience-${i}`}
                      onChange={(e) => updateExperience(i, "title", e.target.value)}
                      placeholder="Senior Software Engineer"
                    />
                  </Field>
                  <Field label="Company" required>
                    <Input
                      required
                      value={exp.company}
                      onChange={(e) => updateExperience(i, "company", e.target.value)}
                      placeholder="Stripe"
                    />
                  </Field>
                  <Field label="Location">
                    <Input
                      value={exp.location ?? ""}
                      onChange={(e) => updateExperience(i, "location", e.target.value)}
                      placeholder="San Francisco, CA"
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
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Education"
            description="One entry per institution. Add coursework when it's relevant to your target roles."
            addLabel="+ Add entry"
            onAdd={addEducation}
            emptyText="No entries yet."
            isEmpty={profile.education.length === 0}
          >
            {profile.education.map((ed, i) => (
              <EntryCard key={i} onRemove={() => removeEducation(i)} removeLabel="Remove entry">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="School" required>
                    <Input
                      required
                      value={ed.school}
                      data-focus-target={`education-${i}`}
                      onChange={(e) => updateEducation(i, "school", e.target.value)}
                      placeholder="Massachusetts Institute of Technology"
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
                      placeholder="Cambridge, MA"
                    />
                  </Field>
                  <Field label="Start">
                    <Input
                      value={ed.start ?? ""}
                      onChange={(e) => updateEducation(i, "start", e.target.value)}
                      placeholder="2014-08"
                    />
                  </Field>
                  <Field label="End / Graduation">
                    <Input
                      value={ed.end ?? ed.graduation ?? ""}
                      onChange={(e) => updateEducation(i, "end", e.target.value)}
                      placeholder="2018-05"
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
                <Field label="Relevant coursework (comma-separated)">
                  <Input
                    value={(ed.coursework ?? []).join(", ")}
                    onChange={(e) =>
                      updateEducation(
                        i,
                        "coursework",
                        e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                      )
                    }
                    placeholder="Algorithms, Distributed Systems, Linear Algebra"
                  />
                </Field>
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Projects"
            description="Side projects, open-source work, or notable professional projects worth their own line."
            addLabel="+ Add project"
            onAdd={addProject}
            emptyText="No projects yet."
            isEmpty={profile.projects.length === 0}
          >
            {profile.projects.map((proj, i) => (
              <EntryCard key={i} onRemove={() => removeProject(i)} removeLabel="Remove project">
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Name" required>
                    <Input
                      required
                      value={proj.name}
                      data-focus-target={`project-${i}`}
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
                        onChange={(e) => updateProject(i, "end_date", e.target.value)}
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
                <Field label="Bullets (one per line)">
                  <Textarea
                    rows={Math.max(2, (proj.bullets ?? []).length + 1)}
                    value={(proj.bullets ?? []).join("\n")}
                    onChange={(e) =>
                      updateProject(
                        i,
                        "bullets",
                        e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                      )
                    }
                  />
                </Field>
                <Field label="Technologies (comma-separated)">
                  <Input
                    value={proj.technologies.join(", ")}
                    onChange={(e) =>
                      updateProject(
                        i,
                        "technologies",
                        e.target.value.split(",").map((s) => s.trim()).filter(Boolean),
                      )
                    }
                    placeholder="TypeScript, Postgres, AWS"
                  />
                </Field>
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Certifications"
            description="Named credentials with an issuer — AWS, Azure, PMP, CPA, Series 7…"
            addLabel="+ Add certification"
            onAdd={addCertification}
            emptyText="No certifications yet."
            isEmpty={profile.certifications.length === 0}
          >
            {profile.certifications.map((cert, i) => (
              <EntryCard
                key={i}
                onRemove={() => removeCertification(i)}
                removeLabel="Remove certification"
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Name" required>
                    <Input
                      required
                      value={cert.name}
                      data-focus-target={`certification-${i}`}
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
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Achievements"
            description="Awards, honours, recognitions — distinct from certifications."
            addLabel="+ Add achievement"
            onAdd={addAchievement}
            emptyText="No achievements yet."
            isEmpty={profile.achievements.length === 0}
          >
            {profile.achievements.map((ach, i) => (
              <EntryCard
                key={i}
                onRemove={() => removeAchievement(i)}
                removeLabel="Remove achievement"
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Title" required>
                    <Input
                      required
                      value={ach.title}
                      data-focus-target={`achievement-${i}`}
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
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Languages"
            description="Spoken / written natural languages. Programming languages live under Skills."
            addLabel="+ Add language"
            onAdd={addLanguage}
            emptyText="No languages yet."
            isEmpty={profile.languages.length === 0}
          >
            {profile.languages.map((lang, i) => (
              <EntryCard
                key={i}
                onRemove={() => removeLanguage(i)}
                removeLabel="Remove language"
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Language" required>
                    <Input
                      required
                      value={lang.name}
                      data-focus-target={`language-${i}`}
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
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Volunteer experience"
            description="Community service, non-profit, pro-bono. Kept separate from paid roles."
            addLabel="+ Add volunteer role"
            onAdd={addVolunteer}
            emptyText="No volunteer roles yet."
            isEmpty={profile.volunteer.length === 0}
          >
            {profile.volunteer.map((vol, i) => (
              <EntryCard
                key={i}
                onRemove={() => removeVolunteer(i)}
                removeLabel="Remove volunteer role"
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Organization" required>
                    <Input
                      required
                      value={vol.organization}
                      data-focus-target={`volunteer-${i}`}
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
                        e.target.value.split("\n").map((s) => s.trim()).filter(Boolean),
                      )
                    }
                  />
                </Field>
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Publications"
            description="Papers, articles, book chapters."
            addLabel="+ Add publication"
            onAdd={addPublication}
            emptyText="No publications yet."
            isEmpty={profile.publications.length === 0}
          >
            {profile.publications.map((pub, i) => (
              <EntryCard
                key={i}
                onRemove={() => removePublication(i)}
                removeLabel="Remove publication"
              >
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="Title" required>
                    <Input
                      required
                      value={pub.title}
                      data-focus-target={`publication-${i}`}
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
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Professional affiliations"
            description="Memberships — IEEE, ACM, a state bar, an honour society."
            addLabel="+ Add affiliation"
            onAdd={addAffiliation}
            emptyText="No affiliations yet."
            isEmpty={profile.affiliations.length === 0}
          >
            {profile.affiliations.map((aff, i) => (
              <EntryCard
                key={i}
                onRemove={() => removeAffiliation(i)}
                removeLabel="Remove affiliation"
              >
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Organization" required>
                    <Input
                      required
                      value={aff.name}
                      data-focus-target={`affiliation-${i}`}
                      onChange={(e) => updateAffiliation(i, "name", e.target.value)}
                      placeholder="IEEE"
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
              </EntryCard>
            ))}
          </SectionShell>

          <SectionShell
            title="Additional sections"
            description="Hobbies, Patents, Conference Talks, Open-Source Contributions — anything else."
            addLabel="+ Add section"
            onAdd={addAdditional}
            emptyText="Nothing here yet."
            isEmpty={profile.additional_sections.length === 0}
          >
            {profile.additional_sections.map((sec, i) => (
              <EntryCard
                key={i}
                onRemove={() => removeAdditional(i)}
                removeLabel="Remove section"
              >
                <Field label="Section heading" required>
                  <Input
                    required
                    value={sec.label}
                    data-focus-target={`additional-${i}`}
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
              </EntryCard>
            ))}
          </SectionShell>

          {/* ── Save bar ── sticky to the bottom so the action stays in
              reach no matter how long the form. */}
          <div
            className="sticky bottom-4 z-10 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-card/90 px-4 py-3 shadow-md backdrop-blur"
            role="region"
            aria-label="Save profile"
          >
            <div className="text-sm" aria-live="polite">
              {loading && <span className="text-muted-foreground">Loading…</span>}
              {!loading && info && <span className="text-muted-foreground">{info}</span>}
              {!loading && !info && !validation.ok && (
                <span className="text-muted-foreground">{validation.message}</span>
              )}
              {error && <span className="text-destructive">{error}</span>}
            </div>
            <Button type="submit" disabled={saving || !validation.ok}>
              {saving ? "Saving…" : "Save profile"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

/** Left-column "who you are" card — calm, low-density, mostly
 * display. Shows the live state of the form so a user editing on
 * the right or middle column sees their changes reflected back
 * instantly. Avatar slot uses initials only (no upload pipeline
 * — keeps the surface focused on text content the tailor flow
 * actually reads). */
function IdentityCard({ profile }: { profile: Profile }) {
  const initials = useMemo(() => {
    const name = profile.name.trim();
    if (!name) return "—";
    const parts = name.split(/\s+/).filter(Boolean);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }, [profile.name]);

  const links = [
    profile.links.linkedin
      ? { href: hrefify(profile.links.linkedin), label: "LinkedIn" }
      : null,
    profile.links.github
      ? { href: hrefify(profile.links.github), label: "GitHub" }
      : null,
    profile.links.website
      ? { href: hrefify(profile.links.website), label: "Portfolio" }
      : null,
  ].filter((x): x is { href: string; label: string } => x !== null);

  return (
    <Card className="h-full border-border/70 shadow-sm">
      <CardContent className="space-y-5 pt-6">
        <div className="flex items-center gap-4">
          <div
            aria-hidden="true"
            className="flex h-16 w-16 shrink-0 items-center justify-center rounded-full bg-accent font-display text-2xl font-medium text-accent-foreground"
          >
            {initials}
          </div>
          <div className="min-w-0 space-y-1">
            <p className="truncate font-display text-xl font-medium tracking-tight">
              {profile.name.trim() || "Your name"}
            </p>
            <p className="truncate text-sm text-muted-foreground">
              {(profile.headline ?? "").trim() || "Your headline"}
            </p>
          </div>
        </div>

        <dl className="space-y-2 text-sm">
          <ContactRow label="Email" value={profile.email} />
          <ContactRow label="Phone" value={profile.phone} />
          <ContactRow label="Location" value={profile.location} />
        </dl>

        {links.length > 0 && (
          <div className="flex flex-wrap gap-2 pt-1">
            {links.map((l) => (
              <a
                key={l.label}
                href={l.href}
                target="_blank"
                rel="noreferrer noopener"
                className="inline-flex items-center rounded-full border border-border bg-background px-3 py-1 text-xs font-medium text-foreground transition-colors hover:border-primary/60 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {l.label}
              </a>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function ContactRow({ label, value }: { label: string; value: string | null | undefined }) {
  const v = (value ?? "").trim();
  return (
    <div className="flex gap-3">
      <dt className="w-16 shrink-0 text-xs uppercase tracking-wide text-muted-foreground">
        {label}
      </dt>
      <dd className={`truncate ${v ? "text-foreground" : "text-muted-foreground/60"}`}>
        {v || "—"}
      </dd>
    </div>
  );
}

/** Normalise a free-form URL into something that's safe to drop in
 * an `href`. Users routinely paste bare hostnames ("linkedin.com/in/…");
 * leaving those as-is sends the browser to a relative path under the
 * current origin. Add `https://` when there's no scheme. */
function hrefify(s: string): string {
  const trimmed = s.trim();
  if (!trimmed) return "#";
  if (/^https?:\/\//i.test(trimmed)) return trimmed;
  return `https://${trimmed}`;
}

/** Wrapper for the full-width résumé sections. Single source of truth
 * for section spacing, heading style, the "+ Add" button position,
 * and the empty-state message — so every section reads the same
 * regardless of which one you're looking at. */
function SectionShell({
  title,
  description,
  addLabel,
  onAdd,
  emptyText,
  isEmpty,
  children,
}: {
  title: string;
  description: string;
  /** Optional — sections that aren't repeatable (Summary, Skills)
   * omit `addLabel` + `onAdd` and just render `children` directly. */
  addLabel?: string;
  onAdd?: () => void;
  emptyText?: string;
  isEmpty?: boolean;
  children: React.ReactNode;
}) {
  return (
    <Card className="border-border/70 shadow-sm">
      <CardHeader className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:space-y-0">
        <div className="space-y-1">
          <CardTitle className="font-display text-xl font-medium tracking-tight">
            {title}
          </CardTitle>
          <CardDescription>{description}</CardDescription>
        </div>
        {addLabel && onAdd && (
          <Button type="button" variant="outline" size="sm" onClick={onAdd}>
            {addLabel}
          </Button>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {isEmpty && emptyText ? (
          <p className="rounded-md border border-dashed border-border bg-secondary/30 px-4 py-6 text-center text-sm text-muted-foreground">
            {emptyText}
          </p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}

/** Container for one row inside a repeatable section. Carries the
 * card chrome + the remove button so the inner JSX stays focused on
 * the fields themselves. */
function EntryCard({
  onRemove,
  removeLabel,
  children,
}: {
  onRemove: () => void;
  removeLabel: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3 rounded-lg border border-border bg-card p-4">
      <div className="space-y-3">{children}</div>
      <div className="flex justify-end">
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={onRemove}
        >
          {removeLabel}
        </Button>
      </div>
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

/** Detect whether the loaded skills are in categorised-group shape
 * (`{category, items}`) vs. the legacy flat string list. Empty arrays
 * default to flat — the user explicitly opts in to grouped via the
 * "Add category" button below. */
function isGroupedSkills(skills: Profile["skills"]): skills is ProfileSkillGroup[] {
  return (
    Array.isArray(skills) &&
    skills.length > 0 &&
    typeof skills[0] === "object" &&
    skills[0] !== null &&
    "items" in (skills[0] as object)
  );
}

/** Skills editor — handles both flat and grouped shapes.
 *
 * Flat (legacy / ungrouped resumes): single comma-separated input
 * + "Convert to categorised" button.
 *
 * Grouped (categorised resumes): N rows, each with a category label
 * input and a comma-separated items input. "+ Add category" creates
 * a new empty row; the per-row "Remove" button drops a category and,
 * when removing the last grouped row, reverts to the flat shape so
 * the empty state is clean. */
function SkillsEditor({
  skills,
  onChange,
}: {
  skills: Profile["skills"];
  onChange: (next: Profile["skills"]) => void;
}) {
  if (isGroupedSkills(skills)) {
    const groups = skills;
    function updateGroup(i: number, patch: Partial<(typeof groups)[number]>) {
      const next = groups.map((g, idx) => (idx === i ? { ...g, ...patch } : g));
      onChange(next);
    }
    function addGroup() {
      onChange([{ category: "", items: [] }, ...groups]);
    }
    function removeGroup(i: number) {
      const next = groups.filter((_, idx) => idx !== i);
      // Empty grouped → fall back to the flat shape so the editor
      // starts fresh next time without an orphan empty group.
      if (next.length === 0) {
        onChange([]);
      } else {
        onChange(next);
      }
    }
    return (
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Skills (by category)</span>
          <Button type="button" variant="outline" size="sm" onClick={addGroup}>
            + Add category
          </Button>
        </div>
        {groups.map((g, i) => (
          <div key={i} className="grid gap-3 sm:grid-cols-[1fr_2fr_auto]">
            <Input
              value={g.category ?? ""}
              onChange={(e) => updateGroup(i, { category: e.target.value })}
              placeholder="Cloud Platforms"
            />
            <Input
              value={g.items.join(", ")}
              onChange={(e) =>
                updateGroup(i, {
                  items: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                })
              }
              placeholder="AWS, Azure, GCP"
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="text-destructive hover:bg-destructive/10 hover:text-destructive"
              onClick={() => removeGroup(i)}
            >
              Remove
            </Button>
          </div>
        ))}
      </div>
    );
  }
  // Flat shape: single comma-separated input + opt-in to grouped.
  const flat = skills as string[];
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">Skills (comma-separated)</span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => {
            // Convert current flat list into a single un-named group
            // so the user can label it. Empty flat → seed an empty
            // group so the inputs render.
            onChange(
              flat.length > 0
                ? [{ category: "", items: flat }]
                : [{ category: "", items: [] }],
            );
          }}
        >
          Convert to categorised
        </Button>
      </div>
      <Input
        value={flat.join(", ")}
        onChange={(e) =>
          onChange(
            e.target.value
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          )
        }
        placeholder="Python, FastAPI, React, AWS"
      />
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
      <span className="text-sm font-medium text-foreground">
        {label}
        {required && <span className="ml-0.5 text-primary">*</span>}
      </span>
      {hint && <span className="block text-xs text-muted-foreground">{hint}</span>}
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
      start: e.start ?? "",
      end: e.end ?? "",
      graduation: e.graduation ?? e.end ?? "",
      gpa: e.gpa ?? "",
      coursework: e.coursework ?? [],
    })),
    projects: (p.projects ?? []).map((pr) => ({
      name: pr.name ?? "",
      description: pr.description ?? "",
      bullets: pr.bullets ?? [],
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
    // Skills: drop empty groups + per-group empty items; preserve
    // whichever shape (flat vs. grouped) is currently active.
    skills: isGroupedSkills(p.skills)
      ? (p.skills
          .map((g) => ({
            category: (g.category ?? "").trim() || null,
            items: g.items.filter((s) => s.trim()),
          }))
          .filter(
            (g) => g.items.length > 0 || (g.category ?? "").trim().length > 0,
          ) as Profile["skills"])
      : ((p.skills as string[]).filter((s) => s && s.trim()) as Profile["skills"]),
    education: p.education.map((ed) => ({
      ...ed,
      field_of_study: opt(ed.field_of_study),
      location: opt(ed.location),
      gpa: opt(ed.gpa),
      // Mirror end → graduation so the legacy tailor-service field
      // stays populated even when the UI edits `end`.
      graduation: (ed.end || ed.graduation || "").trim(),
      coursework: (ed.coursework ?? []).filter((c) => c.trim()),
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
