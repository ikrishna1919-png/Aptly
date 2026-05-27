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
  ParseTimeoutError,
  fetchProfile,
  parseProfileText,
  saveProfile,
  type Profile,
  type ProfileEducation,
  type ProfileExperience,
} from "@/lib/api";

const TOKEN_KEY = "aptly.adminToken";

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

export default function ProfilePage() {
  const [token, setToken] = useState("");
  const [tokenLoaded, setTokenLoaded] = useState(false);
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

  useEffect(() => {
    setToken(localStorage.getItem(TOKEN_KEY) ?? "");
    setTokenLoaded(true);
  }, []);

  const refresh = useCallback(
    async (tok: string) => {
      if (!tok) return;
      setLoading(true);
      setError(null);
      try {
        const data = await fetchProfile(tok);
        setProfile(normaliseProfile(data));
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load profile");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (tokenLoaded && token) void refresh(token);
  }, [tokenLoaded, token, refresh]);

  function saveToken(value: string) {
    setToken(value);
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  }

  async function onParse() {
    if (!token) return setError("Admin token required");
    if (!pasted.trim()) return setError("Paste your resume text first");
    // Cancel any prior in-flight parse before starting a new one —
    // protects against a "retry" double-click triggering two polling
    // loops against the same browser session.
    parseAbortRef.current?.abort();
    const controller = new AbortController();
    parseAbortRef.current = controller;

    setError(null);
    setInfo("Kicking off parse…");
    setParsing(true);
    try {
      const parsed = await parseProfileText(pasted, token, {
        signal: controller.signal,
        onProgress: () => setInfo("Parsing your resume with Claude — this can take up to ~90s."),
      });
      setProfile(normaliseProfile(parsed));
      setInfo("Parsed — review and edit, then click Save.");
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") {
        // Caller-initiated cancel — silent.
        setInfo(null);
      } else if (e instanceof ParseTimeoutError) {
        setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : "Parse failed");
      }
    } finally {
      // Only clear the spinner if THIS controller is still the active
      // one (a fresh retry could have swapped it in mid-flight).
      if (parseAbortRef.current === controller) {
        parseAbortRef.current = null;
        setParsing(false);
      }
    }
  }

  // Stop polling if the page unmounts mid-parse.
  useEffect(() => {
    return () => parseAbortRef.current?.abort();
  }, []);

  async function onSave(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return setError("Admin token required");
    setError(null);
    setInfo(null);
    setSaving(true);
    try {
      const saved = await saveProfile(cleanForSave(profile), token);
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
          <CardTitle>Admin token</CardTitle>
          <CardDescription>
            Stored in this browser only. Same token as <code>/admin</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Input
            type="password"
            placeholder="X-Admin-Token"
            value={token}
            onChange={(e) => saveToken(e.target.value)}
            autoComplete="off"
            spellCheck={false}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Paste resume to autofill</CardTitle>
          <CardDescription>
            We send the text to Claude Sonnet 4.6 with strict
            truthful-only parsing. The result populates the form below for
            review; nothing saves until you click Save.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            rows={10}
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            placeholder="Paste your resume as plain text — copy-paste from PDF or DOCX is fine."
          />
          <div className="flex flex-wrap items-center justify-end gap-3">
            {parsing && (
              <span
                className="inline-flex items-center gap-2 text-xs text-muted-foreground"
                role="status"
                aria-live="polite"
              >
                <Spinner /> Parsing your resume — usually 10–30s, up to ~90s.
              </span>
            )}
            <Button
              type="button"
              onClick={() => void onParse()}
              disabled={parsing || !token || !pasted.trim()}
            >
              {parsing ? "Parsing…" : error ? "Retry parse" : "Parse with Claude"}
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

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm">
                {loading && <span className="text-muted-foreground">Loading…</span>}
                {info && <span className="text-muted-foreground">{info}</span>}
                {error && <span className="text-destructive">{error}</span>}
              </div>
              <Button type="submit" disabled={saving || !token}>
                {saving ? "Saving…" : "Save profile"}
              </Button>
            </div>
          </CardContent>
        </Card>
      </form>
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
