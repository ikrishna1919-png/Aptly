"use client";

import { type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useAuthGate } from "@/lib/use-login-modal";
import { cn } from "@/lib/utils";

/**
 * Apply button for a job. The page is public; the ACTION is gated:
 *   * signed in → opens the source ATS posting in a new tab.
 *   * signed out → opens the login modal with "Sign in to apply".
 *
 * Rendered as a real anchor (so middle-click / open-in-new-tab still work
 * for signed-in users), but a logged-out click is intercepted before the
 * navigation happens.
 */
export function ApplyButton({
  url,
  className,
  size = "default",
  variant = "default",
  children,
}: {
  url: string;
  className?: string;
  size?: "default" | "sm" | "lg";
  variant?: "default" | "secondary" | "outline";
  children: ReactNode;
}) {
  const gate = useAuthGate();
  return (
    <Button asChild size={size} variant={variant} className={cn(className)}>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={(e) => {
          if (!gate("apply")) e.preventDefault();
        }}
      >
        {children}
      </a>
    </Button>
  );
}
