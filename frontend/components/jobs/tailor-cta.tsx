"use client";

import { Button } from "@/components/ui/button";
import { useAuthGate } from "@/lib/use-login-modal";

/**
 * "Tailor my resume for this job" CTA on the job detail pane. The page is
 * public; the action is gated:
 *   * signed in → smooth-scrolls to the embedded TailorPanel (`#tailor-panel`).
 *   * signed out → opens the login modal with "Sign in to tailor your resume".
 */
export function TailorCta() {
  const gate = useAuthGate();
  return (
    <Button
      size="lg"
      variant="secondary"
      className="w-full font-semibold sm:w-auto"
      onClick={() => {
        if (!gate("tailor")) return;
        document
          .getElementById("tailor-panel")
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      }}
    >
      Tailor my resume for this job
    </Button>
  );
}
