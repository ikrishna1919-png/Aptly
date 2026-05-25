import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function JobNotFound() {
  return (
    <div className="container max-w-xl py-20 text-center">
      <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
        404
      </p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight">
        Job not found
      </h1>
      <p className="mx-auto mt-2 max-w-sm text-sm text-muted-foreground">
        This posting may have aged out of the 48-hour window, or the link
        is wrong. The feed always shows the freshest roles.
      </p>
      <Button asChild className="mt-6 rounded-full">
        <Link href="/">Back to jobs</Link>
      </Button>
    </div>
  );
}
