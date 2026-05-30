"use client";

import { useEffect, useState } from "react";
import { Laptop, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  type ExtensionSession,
  type SavedQA,
  deleteSavedQA,
  listExtensionSessions,
  listSavedQA,
  revokeExtensionSession,
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
