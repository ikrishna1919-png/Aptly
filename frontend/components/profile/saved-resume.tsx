"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  type ActiveResume,
  activeResumeDownloadUrl,
  deleteActiveResume,
  getActiveResume,
  uploadActiveResume,
} from "@/lib/api";

/**
 * Profile "Saved resume" section: one active resume per user. Empty state →
 * upload; saved state → filename + date + Replace/Delete/Download. Replace and
 * Delete confirm first. Best-effort: a failed fetch renders the empty state
 * rather than breaking the profile page.
 */
export function SavedResume() {
  const [state, setState] = useState<ActiveResume | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getActiveResume()
      .then(setState)
      .catch(() => setState({ present: false }));
  }, []);

  async function onPick(file: File) {
    // DOCX only — the saved resume drives in-place keyword tailoring, which
    // edits the .docx's existing text. A PDF can't be edited that way.
    if (!file.name.toLowerCase().endsWith(".docx")) {
      setError("Upload a Word .docx file. PDF isn't supported — export to .docx and try again.");
      return;
    }
    if (state?.present && !window.confirm("This will replace your current saved resume. Continue?")) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setState(await uploadActiveResume(file));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  async function onDelete() {
    if (!window.confirm("Delete your saved resume?")) return;
    setBusy(true);
    setError(null);
    try {
      await deleteActiveResume();
      setState({ present: false });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setBusy(false);
    }
  }

  if (state === null) return null;

  return (
    <section>
      <h2 className="font-display text-lg font-semibold">Saved resume</h2>
      <p className="mt-1 text-sm text-muted-foreground">
        One resume on file, reused across the ATS Toolkit (format matching + builder).
      </p>

      {state.present ? (
        <div className="mt-4 flex flex-col gap-3 rounded-lg border border-border bg-card p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-3">
            <FileText className="h-6 w-6 shrink-0 text-primary" aria-hidden />
            <div>
              <p className="text-sm font-medium">{state.filename}</p>
              <p className="text-xs text-muted-foreground">
                DOCX
                {state.uploaded_at
                  ? ` · uploaded ${new Date(state.uploaded_at).toLocaleDateString()}`
                  : ""}
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" disabled={busy} onClick={() => inputRef.current?.click()}>
              Replace
            </Button>
            <Button variant="ghost" size="sm" disabled={busy} onClick={() => void onDelete()}>
              Delete
            </Button>
            <Button asChild size="sm">
              <a href={activeResumeDownloadUrl()}>Download</a>
            </Button>
          </div>
        </div>
      ) : (
        <button
          onClick={() => inputRef.current?.click()}
          disabled={busy}
          className="mt-4 flex w-full flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-border bg-card/50 p-8 text-center transition-colors hover:border-primary/40 hover:bg-primary-soft/30"
        >
          <Upload className="h-7 w-7 text-muted-foreground" aria-hidden />
          <span className="text-sm font-medium">Upload your resume (.docx)</span>
          <span className="text-xs text-muted-foreground">Word .docx only — PDF isn&apos;t supported</span>
          {busy && <span className="text-xs text-primary">Uploading…</span>}
        </button>
      )}

      <input
        ref={inputRef}
        type="file"
        accept=".docx"
        className="hidden"
        onChange={(e) => e.target.files?.[0] && onPick(e.target.files[0])}
      />
      {error && <p className="mt-3 text-sm text-destructive">{error}</p>}
    </section>
  );
}
