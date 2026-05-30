import { Suspense } from "react";

import { AtsHub } from "@/components/ats/ats-hub";

// #3 ATS Resume Generator — the existing tailor flow, relocated here from
// /ats. The component reads ?jobId/?option/?step via useSearchParams, so it's
// wrapped in Suspense.
export default function ATSGeneratePage() {
  return (
    <Suspense fallback={null}>
      <AtsHub />
    </Suspense>
  );
}
