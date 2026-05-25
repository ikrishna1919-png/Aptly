import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

export function JobFeedSkeleton({ count = 6 }: { count?: number }) {
  return (
    <ul className="grid gap-3" aria-label="Loading jobs" aria-busy="true">
      {Array.from({ length: count }).map((_, i) => (
        <li key={i}>
          <Card className="space-y-3 p-5">
            <div className="flex items-start gap-3">
              <Skeleton className="h-9 w-9 rounded-md" />
              <div className="flex-1 space-y-2">
                <Skeleton className="h-3 w-24" />
                <Skeleton className="h-4 w-2/3" />
              </div>
              <Skeleton className="h-3 w-12" />
            </div>
            <div className="flex gap-1.5">
              <Skeleton className="h-5 w-20 rounded-full" />
              <Skeleton className="h-5 w-16 rounded-full" />
              <Skeleton className="h-5 w-24 rounded-full" />
            </div>
            <div className="flex gap-1">
              <Skeleton className="h-5 w-14 rounded-md" />
              <Skeleton className="h-5 w-12 rounded-md" />
              <Skeleton className="h-5 w-16 rounded-md" />
            </div>
          </Card>
        </li>
      ))}
    </ul>
  );
}
