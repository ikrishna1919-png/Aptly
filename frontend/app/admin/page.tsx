"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { CompanyMark } from "@/components/company-mark";
import { EmptyState, ErrorState } from "@/components/empty-state";
import {
  MANUAL_SOURCE,
  createManualJob,
  deleteManualJob,
  fetchJobs,
  type Job,
  type ManualJobInput,
} from "@/lib/api";

const TOKEN_KEY = "aptly.adminToken";

const EMPTY_FORM = {
  title: "",
  company: "",
  apply_url: "",
  location: "",
  remote: "",
  employment_type: "",
  salary: "",
  skills: "",
  description: "",
};

type FormState = typeof EMPTY_FORM;

export default function AdminPage() {
  const [token, setToken] = useState("");
  const [tokenLoaded, setTokenLoaded] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [manualJobs, setManualJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  useEffect(() => {
    setToken(localStorage.getItem(TOKEN_KEY) ?? "");
    setTokenLoaded(true);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchJobs({ limit: 200 });
      setManualJobs(data.jobs.filter((j) => j.source === MANUAL_SOURCE));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load jobs");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (tokenLoaded) void refresh();
  }, [tokenLoaded, refresh]);

  function saveToken(value: string) {
    setToken(value);
    if (value) localStorage.setItem(TOKEN_KEY, value);
    else localStorage.removeItem(TOKEN_KEY);
  }

  function update<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setInfo(null);
    if (!token) return setError("Admin token required");

    const payload: ManualJobInput = {
      title: form.title.trim(),
      company: form.company.trim(),
      apply_url: form.apply_url.trim(),
    };
    if (form.location.trim()) payload.location = form.location.trim();
    if (form.employment_type.trim()) payload.employment_type = form.employment_type.trim();
    if (form.salary.trim()) payload.salary = form.salary.trim();
    if (form.description.trim()) payload.description = form.description.trim();
    if (form.remote === "true") payload.remote = true;
    else if (form.remote === "false") payload.remote = false;
    if (form.skills.trim()) {
      payload.skills = form.skills.split(",").map((s) => s.trim()).filter(Boolean);
    }

    setSubmitting(true);
    try {
      await createManualJob(payload, token);
      setForm(EMPTY_FORM);
      setInfo(`Added “${payload.title}”`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSubmitting(false);
    }
  }

  async function onDelete(job: Job) {
    if (!token) return setError("Admin token required");
    if (!confirm(`Delete "${job.title}"?`)) return;
    setError(null);
    setInfo(null);
    try {
      await deleteManualJob(job.id, token);
      setInfo(`Deleted “${job.title}”`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <div className="container max-w-3xl space-y-8 py-10">
      <header className="space-y-3">
        <div className="flex items-center gap-2">
          <Badge variant="default">Admin</Badge>
          <Link
            href="/jobs"
            className="text-sm text-muted-foreground hover:text-foreground"
          >
            ← back to feed
          </Link>
          <Link
            href="/profile"
            className="ml-auto text-sm text-muted-foreground hover:text-foreground"
          >
            profile →
          </Link>
        </div>
        <h1 className="text-3xl font-semibold tracking-tight">Manual jobs</h1>
        <p className="max-w-xl text-sm text-muted-foreground">
          Jobs added here are stored with{" "}
          <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[12px]">
            source=&quot;manual&quot;
          </code>{" "}
          and persist until you delete them — the 48-hour rolling cleanup
          skips them. They appear in the public feed alongside ATS-ingested
          postings.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Admin token</CardTitle>
          <CardDescription>
            Stored in this browser only (localStorage). Must match{" "}
            <code className="rounded bg-muted px-1 font-mono text-[12px]">
              ADMIN_TOKEN
            </code>{" "}
            on the backend.
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
          <CardTitle>Add a job</CardTitle>
          <CardDescription>
            Required fields marked with <span className="text-primary">*</span>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Title" required>
                <Input
                  required
                  value={form.title}
                  onChange={(e) => update("title", e.target.value)}
                />
              </Field>
              <Field label="Company" required>
                <Input
                  required
                  value={form.company}
                  onChange={(e) => update("company", e.target.value)}
                />
              </Field>
              <Field label="Apply URL" required className="sm:col-span-2">
                <Input
                  required
                  type="url"
                  placeholder="https://example.com/apply/…"
                  value={form.apply_url}
                  onChange={(e) => update("apply_url", e.target.value)}
                />
              </Field>
              <Field label="Location">
                <Input
                  placeholder="San Francisco / Remote / …"
                  value={form.location}
                  onChange={(e) => update("location", e.target.value)}
                />
              </Field>
              <Field label="Workplace">
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                  value={form.remote}
                  onChange={(e) => update("remote", e.target.value)}
                >
                  <option value="">(unknown)</option>
                  <option value="true">Remote</option>
                  <option value="false">On-site</option>
                </select>
              </Field>
              <Field label="Employment type">
                <Input
                  placeholder="Full-time, Contract, …"
                  value={form.employment_type}
                  onChange={(e) => update("employment_type", e.target.value)}
                />
              </Field>
              <Field label="Salary">
                <Input
                  placeholder="$180k–$220k"
                  value={form.salary}
                  onChange={(e) => update("salary", e.target.value)}
                />
              </Field>
              <Field label="Skills (comma-separated)" className="sm:col-span-2">
                <Input
                  placeholder="Python, React, Postgres"
                  value={form.skills}
                  onChange={(e) => update("skills", e.target.value)}
                />
              </Field>
            </div>

            <Field label="Description / JD">
              <Textarea
                rows={6}
                value={form.description}
                onChange={(e) => update("description", e.target.value)}
              />
            </Field>

            <Separator />

            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="text-sm">
                {info && <span className="text-muted-foreground">{info}</span>}
                {error && <span className="text-destructive">{error}</span>}
              </div>
              <Button type="submit" disabled={submitting}>
                {submitting ? "Adding…" : "Add job"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold tracking-tight">
            Manual jobs{" "}
            <span className="text-muted-foreground">({manualJobs.length})</span>
          </h2>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void refresh()}
            disabled={loading}
          >
            {loading ? "Refreshing…" : "Refresh"}
          </Button>
        </div>

        {!loading && error && <ErrorState description={error} />}

        {!loading && !error && manualJobs.length === 0 && (
          <EmptyState
            title="No manual jobs yet"
            description="Use the form above to add your first one."
          />
        )}

        {manualJobs.length > 0 && (
          <ul className="grid gap-2">
            {manualJobs.map((job) => (
              <li key={job.id}>
                <Card className="flex items-start gap-4 p-4">
                  <CompanyMark name={job.company} size="sm" />
                  <div className="min-w-0 flex-1 space-y-1">
                    <Link
                      href={`/jobs/${job.id}`}
                      className="block truncate font-medium hover:underline"
                    >
                      {job.title}
                    </Link>
                    <p className="truncate text-xs text-muted-foreground">
                      {job.company}
                      {job.location ? ` · ${job.location}` : ""}
                      {job.salary ? ` · ${job.salary}` : ""}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => void onDelete(job)}
                    className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                    aria-label={`Delete ${job.title}`}
                  >
                    Delete
                  </Button>
                </Card>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function Field({
  label,
  required,
  className,
  children,
}: {
  label: string;
  required?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={`block space-y-1.5 ${className ?? ""}`}>
      <span className="text-sm font-medium">
        {label}
        {required && <span className="ml-0.5 text-primary">*</span>}
      </span>
      {children}
    </label>
  );
}
