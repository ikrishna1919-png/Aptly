"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // Load saved token on mount.
  useEffect(() => {
    setToken(localStorage.getItem(TOKEN_KEY) ?? "");
    setTokenLoaded(true);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Pull a wide slice and filter to manual rows client-side. The public
      // feed already includes manual jobs regardless of the rolling window.
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

    if (!token) {
      setError("Admin token required");
      return;
    }

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
      payload.skills = form.skills
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
    }

    try {
      await createManualJob(payload, token);
      setForm(EMPTY_FORM);
      setInfo(`Added "${payload.title}"`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Create failed");
    }
  }

  async function onDelete(job: Job) {
    if (!token) {
      setError("Admin token required");
      return;
    }
    if (!confirm(`Delete "${job.title}"?`)) return;
    setError(null);
    setInfo(null);
    try {
      await deleteManualJob(job.id, token);
      setInfo(`Deleted "${job.title}"`);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed");
    }
  }

  return (
    <main className="container mx-auto max-w-3xl space-y-8 py-10">
      <header className="space-y-3">
        <div className="flex items-center gap-2">
          <Badge variant="secondary">Admin</Badge>
          <Link href="/" className="text-sm text-muted-foreground hover:underline">
            ← back to feed
          </Link>
        </div>
        <h1 className="text-3xl font-bold tracking-tight">Manual jobs</h1>
        <p className="text-muted-foreground">
          Jobs added here are stored with{" "}
          <code className="rounded bg-muted px-1">source=&quot;manual&quot;</code>{" "}
          and persist until you delete them — the 48-hour rolling cleanup
          skips them.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Admin token</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          <Input
            type="password"
            placeholder="X-Admin-Token"
            value={token}
            onChange={(e) => saveToken(e.target.value)}
            autoComplete="off"
          />
          <p className="text-xs text-muted-foreground">
            Stored in localStorage. Must match the backend&apos;s{" "}
            <code className="rounded bg-muted px-1">ADMIN_TOKEN</code> env var.
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Add a job</CardTitle>
        </CardHeader>
        <CardContent>
          <form onSubmit={onSubmit} className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Title *">
                <Input
                  required
                  value={form.title}
                  onChange={(e) => update("title", e.target.value)}
                />
              </Field>
              <Field label="Company *">
                <Input
                  required
                  value={form.company}
                  onChange={(e) => update("company", e.target.value)}
                />
              </Field>
              <Field label="Apply URL *">
                <Input
                  required
                  type="url"
                  value={form.apply_url}
                  onChange={(e) => update("apply_url", e.target.value)}
                />
              </Field>
              <Field label="Location">
                <Input
                  value={form.location}
                  onChange={(e) => update("location", e.target.value)}
                />
              </Field>
              <Field label="Remote?">
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={form.remote}
                  onChange={(e) => update("remote", e.target.value)}
                >
                  <option value="">(unknown)</option>
                  <option value="true">Yes</option>
                  <option value="false">No</option>
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
              <Field label="Skills (comma-separated)">
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

            <div className="flex items-center gap-3">
              <Button type="submit">Add job</Button>
              {info && <span className="text-sm text-muted-foreground">{info}</span>}
              {error && <span className="text-sm text-destructive">{error}</span>}
            </div>
          </form>
        </CardContent>
      </Card>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold">
            Manual jobs ({manualJobs.length})
          </h2>
          <Button variant="outline" size="sm" onClick={() => void refresh()}>
            Refresh
          </Button>
        </div>

        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {!loading && manualJobs.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No manual jobs yet. Add one above.
          </p>
        )}

        <ul className="space-y-2">
          {manualJobs.map((job) => (
            <li
              key={job.id}
              className="flex items-start justify-between gap-4 rounded-md border p-3"
            >
              <div className="space-y-1">
                <a
                  href={job.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-medium hover:underline"
                >
                  {job.title}
                </a>
                <p className="text-xs text-muted-foreground">
                  {job.company}
                  {job.location ? ` · ${job.location}` : ""}
                  {job.salary ? ` · ${job.salary}` : ""}
                </p>
              </div>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => void onDelete(job)}
              >
                Delete
              </Button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-sm font-medium">{label}</span>
      {children}
    </label>
  );
}
