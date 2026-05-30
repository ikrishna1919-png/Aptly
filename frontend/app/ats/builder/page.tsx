"use client";

import { useState } from "react";
import Link from "next/link";
import { FilePlus, Linkedin, Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAuthGate } from "@/lib/use-login-modal";
import { linkedinImport, parseAtsUpload } from "@/lib/ats";

type Tab = "menu" | "linkedin" | "reformat";

export default function ResumeBuilderPage() {
  const [tab, setTab] = useState<Tab>("menu");

  if (tab === "linkedin") return <LinkedInImport onBack={() => setTab("menu")} />;
  if (tab === "reformat") return <ImportReformat onBack={() => setTab("menu")} />;

  return (
    <div className="container max-w-3xl py-10">
      <h1 className="font-display text-2xl font-bold tracking-tight">Resume builder</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Build a resume from scratch, import from LinkedIn, or reformat one you already have. New
        resumes use your default format.
      </p>

      <div className="mt-6 space-y-3">
        <Card
          icon={FilePlus}
          title="Create new from profile"
          blurb="Generate a clean resume from your Aptly profile (no job tailoring), in your default format."
        >
          {/* Reuse the generator: a generic resume is a generation with no JD —
              the generator handles the editable preview + download. */}
          <Button asChild>
            <Link href="/ats/generate">Open generator →</Link>
          </Button>
        </Card>

        <Card
          icon={Linkedin}
          title="Import from LinkedIn"
          blurb="Upload your LinkedIn data export (a ZIP you request from LinkedIn). We read it locally on the server — no scraping, no LinkedIn login."
        >
          <Button onClick={() => setTab("linkedin")}>Import →</Button>
        </Card>

        <Card
          icon={Upload}
          title="Import and reformat"
          blurb="Upload an existing resume (DOCX or PDF). We parse the content and re-render it in your default format."
        >
          <Button onClick={() => setTab("reformat")}>Upload →</Button>
        </Card>
      </div>
    </div>
  );
}

function LinkedInImport({ onBack }: { onBack: () => void }) {
  const gate = useAuthGate();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ imported: Record<string, unknown> } | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onFile(file: File) {
    if (!gate("tailor")) return;
    setBusy(true);
    setError(null);
    try {
      const res = await linkedinImport(file);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Import failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container max-w-2xl py-10">
      <button onClick={onBack} className="mb-3 text-sm text-muted-foreground hover:text-foreground">
        ← Back
      </button>
      <h1 className="font-display text-2xl font-bold tracking-tight">Import from LinkedIn</h1>
      <ol className="mt-4 space-y-2 text-sm text-muted-foreground">
        <li>
          <span className="font-medium text-foreground">Step 1.</span> On LinkedIn, go to Settings
          → Data privacy → <em>Get a copy of your data</em>, and request your archive. LinkedIn
          emails you a ZIP (usually within 24 hours).
        </li>
        <li>
          <span className="font-medium text-foreground">Step 2.</span> Upload that ZIP here.
        </li>
        <li>
          <span className="font-medium text-foreground">Step 3.</span> Review what we found and
          merge it into your profile.
        </li>
      </ol>

      <label className="mt-6 flex w-full cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-border bg-card/50 p-10 text-center hover:border-primary/40">
        <Upload className="h-8 w-8 text-muted-foreground" aria-hidden />
        <span className="text-sm font-medium">Upload your LinkedIn data archive (ZIP)</span>
        {busy && <span className="text-xs text-primary">Reading…</span>}
        <input
          type="file"
          accept=".zip"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
        />
      </label>

      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
      {result && (
        <div className="mt-6 rounded-xl border border-border bg-card p-5">
          <p className="text-sm font-semibold">Found in your export:</p>
          <pre className="mt-2 max-h-64 overflow-auto rounded-md bg-secondary/50 p-3 text-xs">
            {JSON.stringify(result.imported, null, 2)}
          </pre>
          <p className="mt-3 text-xs text-muted-foreground">
            Head to your{" "}
            <Link href="/profile" className="text-primary underline-offset-4 hover:underline">
              profile
            </Link>{" "}
            to review and save these into your sections.
          </p>
        </div>
      )}
    </div>
  );
}

function ImportReformat({ onBack }: { onBack: () => void }) {
  const gate = useAuthGate();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onFile(file: File) {
    if (!gate("tailor")) return;
    setBusy(true);
    setError(null);
    try {
      const res = await parseAtsUpload(file);
      setNote(
        res.kind === "docx"
          ? "DOCX received — open the generator to tailor + re-render it in your default format."
          : "PDF received — we'll extract the content and render it in your default format in the generator.",
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="container max-w-2xl py-10">
      <button onClick={onBack} className="mb-3 text-sm text-muted-foreground hover:text-foreground">
        ← Back
      </button>
      <h1 className="font-display text-2xl font-bold tracking-tight">Import and reformat</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Upload an existing resume; we render it in your default format. DOCX preserves formatting
        reliably; PDF content is extracted (formatting can&apos;t be preserved exactly).
      </p>
      <label className="mt-6 flex w-full cursor-pointer flex-col items-center gap-2 rounded-2xl border-2 border-dashed border-border bg-card/50 p-10 text-center hover:border-primary/40">
        <Upload className="h-8 w-8 text-muted-foreground" aria-hidden />
        <span className="text-sm font-medium">Upload DOCX or PDF</span>
        {busy && <span className="text-xs text-primary">Uploading…</span>}
        <input
          type="file"
          accept=".docx,.pdf"
          className="hidden"
          onChange={(e) => e.target.files?.[0] && onFile(e.target.files[0])}
        />
      </label>
      {error && <p className="mt-4 text-sm text-destructive">{error}</p>}
      {note && (
        <p className="mt-4 rounded-md border border-primary/15 bg-primary-soft/40 px-3 py-2 text-sm">
          {note}{" "}
          <Link href="/ats/generate" className="font-medium text-primary underline-offset-4 hover:underline">
            Open generator →
          </Link>
        </p>
      )}
    </div>
  );
}

function Card({
  icon: Icon,
  title,
  blurb,
  children,
}: {
  icon: typeof FilePlus;
  title: string;
  blurb: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-card p-5 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex items-start gap-3">
        <Icon className="mt-0.5 h-6 w-6 shrink-0 text-primary" aria-hidden />
        <div>
          <p className="font-semibold">{title}</p>
          <p className="text-sm text-muted-foreground">{blurb}</p>
        </div>
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
