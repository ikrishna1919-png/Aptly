import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import type { Job } from "@/lib/api";

const RELATIVE = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const deltaSec = Math.round((then - Date.now()) / 1000);
  const abs = Math.abs(deltaSec);
  if (abs < 60) return RELATIVE.format(deltaSec, "second");
  if (abs < 3600) return RELATIVE.format(Math.round(deltaSec / 60), "minute");
  if (abs < 86400) return RELATIVE.format(Math.round(deltaSec / 3600), "hour");
  return RELATIVE.format(Math.round(deltaSec / 86400), "day");
}

export function JobCard({ job }: { job: Job }) {
  return (
    <Card>
      <CardHeader className="space-y-2">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <h2 className="text-lg font-semibold leading-tight">
              <a
                href={job.url}
                target="_blank"
                rel="noopener noreferrer"
                className="hover:underline"
              >
                {job.title}
              </a>
            </h2>
            <p className="text-sm text-muted-foreground">
              {job.company}
              {job.location ? ` · ${job.location}` : ""}
            </p>
          </div>
          <span className="whitespace-nowrap text-xs text-muted-foreground">
            {relativeTime(job.source_updated_at)}
          </span>
        </div>

        <div className="flex flex-wrap gap-1.5">
          {job.remote === true && <Badge variant="secondary">Remote</Badge>}
          {job.remote === false && <Badge variant="outline">On-site</Badge>}
          {job.employment_type && (
            <Badge variant="outline">{job.employment_type}</Badge>
          )}
          {job.sponsors_visa === true && <Badge>Sponsors visa</Badge>}
          <Badge variant="outline" className="opacity-70">
            via {job.source}
          </Badge>
        </div>
      </CardHeader>

      {job.skills.length > 0 && (
        <CardContent className="pt-0">
          <div className="flex flex-wrap gap-1.5">
            {job.skills.slice(0, 12).map((s) => (
              <Badge key={s} variant="secondary" className="font-normal">
                {s}
              </Badge>
            ))}
          </div>
        </CardContent>
      )}
    </Card>
  );
}
