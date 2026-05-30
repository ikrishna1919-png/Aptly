"use client";

import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { useAuthGate } from "@/lib/use-login-modal";

/**
 * "Tailor my resume for this job" CTA on the job detail page.
 *
 * FIX 6: routes to the ATS Resume Generator with the JD pre-filled
 * (`/ats/generate?jobId=…`). The generator skips the format-selection step
 * and applies the user's saved default format (falling back to Modern), so
 * the user lands directly on the customization questions. Auth-gated: a
 * logged-out click opens the login modal first.
 */
export function TailorCta({ jobId }: { jobId: number }) {
  const gate = useAuthGate();
  const router = useRouter();
  return (
    <Button
      size="lg"
      variant="secondary"
      className="w-full font-semibold sm:w-auto"
      onClick={() => {
        if (!gate("tailor")) return;
        router.push(`/ats/generate?jobId=${jobId}`);
      }}
    >
      Tailor my resume for this job
    </Button>
  );
}
