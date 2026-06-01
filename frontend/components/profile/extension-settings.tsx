"use client";

import { useEffect, useState } from "react";
import { Laptop, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type ExtensionSession,
  type Profile,
  type SavedQA,
  deleteSavedQA,
  fetchProfile,
  listExtensionSessions,
  listSavedQA,
  revokeExtensionSession,
  saveProfile,
  updateSavedQA,
} from "@/lib/api";

/**
 * Two profile sections for the browser extension:
 *   * Connected devices — list + revoke extension sessions.
 *   * Saved answers — review/edit/delete the clustered Q&A the extension has
 *     learned. Both are best-effort: if the backend isn't reachable or the
 *     user has nothing yet, the sections render an empty state rather than
 *     erroring the whole profile page.
 */
export function ExtensionSettings() {
  return (
    <div className="space-y-10">
      <ConnectedDevices />
      <FormFillingGuide />
      <SavedAnswers />
    </div>
  );
}

function ConnectedDevices() {
  const [sessions, setSessions] = useState<ExtensionSession[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    listExtensionSessions()
      .then(setSessions)
      .catch(() => setSessions([]));
  }, []);

  async function revoke(id: string) {
    setBusy(id);
    try {
      await revokeExtensionSession(id);
      setSessions((s) => s?.map((x) => (x.id === id ? { ...x, revoked: true } : x)) ?? null);
    } finally {
      setBusy(null);
    }
  }

  if (sessions === null) return null;

  return (
    <section>
      <h2 className="font-display text-lg font-semibold">Connected devices</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Browsers linked to the Aptly extension. Revoke any you don&apos;t recognize.
      </p>
      {sessions.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          No devices connected yet. Install the Aptly extension and sign in to link one.
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {sessions.map((s) => (
            <li
              key={s.id}
              className="flex items-center justify-between gap-3 rounded-lg border border-border p-3"
            >
              <div className="flex min-w-0 items-center gap-3">
                <Laptop className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">{s.device_name || "Browser"}</p>
                  <p className="text-xs text-muted-foreground">
                    Added {new Date(s.created_at).toLocaleDateString()}
                    {s.last_used_at
                      ? ` · last used ${new Date(s.last_used_at).toLocaleDateString()}`
                      : ""}
                  </p>
                </div>
              </div>
              {s.revoked ? (
                <span className="text-xs text-muted-foreground">Revoked</span>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy === s.id}
                  onClick={() => void revoke(s.id)}
                >
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function SavedAnswers() {
  const [items, setItems] = useState<SavedQA[] | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");

  useEffect(() => {
    listSavedQA()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  async function save(id: string) {
    const next = await updateSavedQA(id, { answer: draft });
    setItems((arr) => arr?.map((x) => (x.id === id ? next : x)) ?? null);
    setEditing(null);
  }

  async function remove(id: string) {
    await deleteSavedQA(id);
    setItems((arr) => arr?.filter((x) => x.id !== id) ?? null);
  }

  if (items === null) return null;

  return (
    <section>
      <h2 className="font-display text-lg font-semibold">Saved answers</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        Answers the extension learned while filling applications, reused on future forms. Edit or
        delete anything that&apos;s out of date.
      </p>
      {items.length === 0 ? (
        <p className="mt-4 rounded-lg border border-dashed border-border p-4 text-sm text-muted-foreground">
          Nothing saved yet. As you fill applications with the extension, your answers to recurring
          questions show up here.
        </p>
      ) : (
        <ul className="mt-4 space-y-2">
          {items.map((qa) => (
            <li key={qa.id} className="rounded-lg border border-border p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{qa.question_canonical}</p>
                  {editing === qa.id ? (
                    <div className="mt-2 flex items-center gap-2">
                      <Input value={draft} onChange={(e) => setDraft(e.target.value)} className="text-sm" />
                      <Button size="sm" onClick={() => void save(qa.id)}>
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </div>
                  ) : (
                    <p className="mt-1 text-sm text-muted-foreground">{qa.answer}</p>
                  )}
                  <p className="mt-1 text-xs text-muted-foreground/70">
                    Used {qa.times_used}× · {qa.field_type}
                  </p>
                </div>
                {editing !== qa.id && (
                  <div className="flex items-center gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        setEditing(qa.id);
                        setDraft(qa.answer);
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      aria-label="Delete saved answer"
                      onClick={() => void remove(qa.id)}
                    >
                      <Trash2 className="h-4 w-4" aria-hidden />
                    </Button>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

// ── Form-filling guide ────────────────────────────────────────────────────────
// One place to set the compliance/EEO answers the extension echoes into ATS
// forms. Saved on the profile JSON via the existing PUT /profile. The four EEO
// fields DEFAULT TO BLANK ("Decline / prefer not to say") and are only filled
// by the extension when the user explicitly picks a value here.

const SPONSORSHIP_OPTS = ["", "No", "Yes"];
const WORK_AUTH_OPTS = [
  "",
  "Authorized to work in the US",
  "Not authorized — require sponsorship",
];
// Standard ATS self-identification option wording. "" renders as the blank /
// decline default so nothing is ever auto-selected for the user.
const VETERAN_OPTS = [
  "",
  "I am not a protected veteran",
  "I identify as one or more of the classifications of a protected veteran",
  "I don't wish to answer",
];
const DISABILITY_OPTS = ["", "Yes", "No", "I do not want to answer"];
const RACE_OPTS = [
  "",
  "Hispanic or Latino",
  "White",
  "Black or African American",
  "Asian",
  "Native Hawaiian or Other Pacific Islander",
  "American Indian or Alaska Native",
  "Two or more races",
  "I do not wish to self-identify",
];
const GENDER_OPTS = ["", "Male", "Female", "Non-binary", "I do not wish to self-identify"];

type ComplianceKey =
  | "requires_sponsorship"
  | "work_authorization"
  | "veteran_status"
  | "disability_status"
  | "race_ethnicity"
  | "gender";

function FormFillingGuide() {
  const [profile, setProfile] = useState<Profile | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedNote, setSavedNote] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchProfile()
      .then(setProfile)
      .catch(() => setError("Couldn't load your profile."));
  }, []);

  function set(key: ComplianceKey, value: string) {
    setProfile((p) => (p ? { ...p, [key]: value } : p));
    setSavedNote(false);
  }

  async function save() {
    if (!profile) return;
    setSaving(true);
    setError(null);
    try {
      const next = await saveProfile(profile);
      setProfile(next);
      setSavedNote(true);
    } catch {
      setError("Couldn't save. Try again.");
    } finally {
      setSaving(false);
    }
  }

  if (error && !profile) {
    return (
      <section>
        <h2 className="font-display text-lg font-semibold">Form-filling guide</h2>
        <p className="mt-1 text-sm text-destructive">{error}</p>
      </section>
    );
  }
  if (!profile) return null;

  return (
    <section>
      <h2 className="font-display text-lg font-semibold">Form-filling guide</h2>
      <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
        Set these once and the Aptly extension fills them on supported job applications. The
        demographic (EEO) answers below are <strong>optional and blank by default</strong> — the
        extension only fills one if you explicitly choose it here, and never guesses.
      </p>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <GuideField
          label="Do you require visa sponsorship?"
          value={profile.requires_sponsorship ?? ""}
          opts={SPONSORSHIP_OPTS}
          onChange={(v) => set("requires_sponsorship", v)}
        />
        <GuideField
          label="Work authorization"
          value={profile.work_authorization ?? ""}
          opts={WORK_AUTH_OPTS}
          onChange={(v) => set("work_authorization", v)}
        />
      </div>

      <h3 className="mt-6 text-sm font-semibold">
        Voluntary self-identification (EEO){" "}
        <span className="font-normal text-muted-foreground">— optional, blank by default</span>
      </h3>
      <div className="mt-2 grid gap-4 sm:grid-cols-2">
        <GuideField
          label="Protected veteran status"
          value={profile.veteran_status ?? ""}
          opts={VETERAN_OPTS}
          onChange={(v) => set("veteran_status", v)}
        />
        <GuideField
          label="Disability status"
          value={profile.disability_status ?? ""}
          opts={DISABILITY_OPTS}
          onChange={(v) => set("disability_status", v)}
        />
        <GuideField
          label="Race / ethnicity"
          value={profile.race_ethnicity ?? ""}
          opts={RACE_OPTS}
          onChange={(v) => set("race_ethnicity", v)}
        />
        <GuideField
          label="Gender"
          value={profile.gender ?? ""}
          opts={GENDER_OPTS}
          onChange={(v) => set("gender", v)}
        />
      </div>

      <div className="mt-4 flex items-center gap-3">
        <Button disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save form-filling answers"}
        </Button>
        {savedNote && <span className="text-sm text-primary">Saved.</span>}
        {error && <span className="text-sm text-destructive">{error}</span>}
      </div>
    </section>
  );
}

function GuideField({
  label,
  value,
  opts,
  onChange,
}: {
  label: string;
  value: string;
  opts: string[];
  onChange: (v: string) => void;
}) {
  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="font-medium">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {opts.map((o) => (
          <option key={o} value={o}>
            {o === "" ? "Prefer not to say / leave blank" : o}
          </option>
        ))}
      </select>
    </label>
  );
}
