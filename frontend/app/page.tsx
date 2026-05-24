import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

type HealthResponse = {
  status: string;
  environment: string;
  database: string;
};

type HealthState =
  | { ok: true; data: HealthResponse }
  | { ok: false; error: string };

async function fetchHealth(): Promise<HealthState> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
  try {
    const res = await fetch(`${apiUrl}/api/health`, { cache: "no-store" });
    if (!res.ok) {
      return { ok: false, error: `Backend returned ${res.status}` };
    }
    return { ok: true, data: (await res.json()) as HealthResponse };
  } catch (err) {
    return {
      ok: false,
      error: err instanceof Error ? err.message : "Unknown error",
    };
  }
}

export default async function Page() {
  const health = await fetchHealth();

  return (
    <main className="container mx-auto flex min-h-screen max-w-3xl flex-col justify-center gap-8 py-16">
      <header className="space-y-3">
        <Badge variant="secondary">Phase 0 — Foundation</Badge>
        <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">Aptly</h1>
        <p className="text-muted-foreground">
          Aggregates real jobs, filters for what matters (visa sponsorship,
          location, skills), and uses Claude to tailor your resume per role.
        </p>
      </header>

      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Backend status</CardTitle>
            {health.ok ? (
              <Badge>online</Badge>
            ) : (
              <Badge variant="destructive">offline</Badge>
            )}
          </div>
          <CardDescription>
            Live check against <code>/api/health</code>.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {health.ok ? (
            <dl className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <dt className="text-muted-foreground">Status</dt>
                <dd className="font-medium">{health.data.status}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Environment</dt>
                <dd className="font-medium">{health.data.environment}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">Database</dt>
                <dd className="font-medium">{health.data.database}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-sm text-destructive">
              Could not reach the API: {health.error}
            </p>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
