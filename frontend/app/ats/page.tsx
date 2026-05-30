import { Suspense } from "react";

import { AtsHub } from "@/components/ats/ats-hub";

// The ATS resume hub. Public surface; the generate/upload actions gate on
// auth via `useAuthGate`. Wrapped in Suspense because the hub reads
// `useSearchParams` (?option / ?step / ?jobId).
export default function ATSPage() {
  return (
    <Suspense fallback={null}>
      <AtsHub />
    </Suspense>
  );
}
