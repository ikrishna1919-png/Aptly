"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useOpenLogin } from "@/lib/use-login-modal";
import { createExtensionSession } from "@/lib/api";

/**
 * Extension connect bridge. Opened (in a tab) when the user clicks "Sign in
 * with your Aptly account" in the extension popup, which appends
 * `?ext_id=<chrome.runtime.id>`.
 *
 * Two handoff paths, both implemented here:
 *   PRIMARY  — postMessage: `chrome.runtime.sendMessage(ext_id, {type:
 *              "APTLY_CONNECT_TOKEN", token})`. The page origin is in the
 *              extension's `externally_connectable`, so the service worker
 *              receives it, stores it, and replies {ok:true}. We then show a
 *              success screen and auto-close.
 *   FALLBACK — copy/paste: the token is ALWAYS rendered with a Copy button +
 *              instructions, so if postMessage fails (no ext_id, extension not
 *              installed at that id, SW asleep) the user can paste it into the
 *              popup's "Have a connection code?" field.
 *
 * The token is shown once; the backend stores only its hash.
 */

declare global {
  interface Window {
    chrome?: {
      runtime?: {
        sendMessage?: (id: string, msg: unknown, cb?: (resp: unknown) => void) => void;
        lastError?: { message?: string };
      };
    };
  }
}

type Phase = "checking" | "need-login" | "minting" | "connecting" | "success" | "manual" | "error";

export default function ExtensionConnectPage() {
  const { status } = useAuth();
  const openLogin = useOpenLogin();
  const [phase, setPhase] = useState<Phase>("checking");
  const [token, setToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  const handoff = useCallback((rawToken: string) => {
    const params = new URLSearchParams(window.location.search);
    const extId = params.get("ext_id") || params.get("ext"); // accept legacy ?ext
    const sendMessage = window.chrome?.runtime?.sendMessage;

    // No extension id, or messaging unavailable → straight to copy/paste.
    if (!extId || !sendMessage) {
      setPhase("manual");
      return;
    }

    setPhase("connecting");
    let settled = false;
    try {
      sendMessage(extId, { type: "APTLY_CONNECT_TOKEN", token: rawToken }, (response) => {
        settled = true;
        const ok =
          response && typeof response === "object" && (response as { ok?: boolean }).ok === true;
        if (ok) {
          setPhase("success");
          setTimeout(() => window.close(), 2000);
        } else {
          // Extension reachable but rejected, or no response → fallback.
          setPhase("manual");
        }
      });
    } catch {
      setPhase("manual");
    }
    // If the callback never fires (extension not installed at this id), fall
    // back after a short grace period so the user isn't stuck on a spinner.
    setTimeout(() => {
      if (!settled) setPhase((p) => (p === "connecting" ? "manual" : p));
    }, 2000);
  }, []);

  useEffect(() => {
    if (status === "loading") return;
    if (status === "unauthenticated") {
      setPhase("need-login");
      return;
    }
    if (started.current) return; // mint exactly once
    started.current = true;
    (async () => {
      setPhase("minting");
      try {
        const device =
          typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 80) : "Browser";
        const { token: t } = await createExtensionSession(device);
        setToken(t);
        handoff(t);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Couldn't connect");
        setPhase("error");
      }
    })();
  }, [status, handoff]);

  async function copy() {
    if (!token) return;
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — the box is selectable as a fallback */
    }
  }

  return (
    <div className="container flex min-h-[60vh] max-w-md flex-col items-center justify-center py-16 text-center">
      {phase === "checking" || phase === "minting" ? (
        <p className="text-muted-foreground">Preparing your connection…</p>
      ) : phase === "need-login" ? (
        <>
          <h1 className="font-display text-2xl font-bold">Sign in to connect</h1>
          <p className="mt-2 text-muted-foreground">
            Sign in to aptly.fyi, then we&apos;ll link the Aptly browser extension to your account.
          </p>
          <Button
            className="mt-6"
            onClick={() => openLogin(window.location.pathname + window.location.search)}
          >
            Sign in
          </Button>
        </>
      ) : phase === "connecting" ? (
        <>
          <h1 className="font-display text-2xl font-bold">Almost there</h1>
          <p className="mt-2 flex items-center gap-2 text-muted-foreground">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
            Connecting to your extension…
          </p>
        </>
      ) : phase === "success" ? (
        <>
          <h1 className="font-display text-2xl font-bold">Connected!</h1>
          <p className="mt-2 text-muted-foreground">You can close this tab. Closing automatically…</p>
        </>
      ) : phase === "error" ? (
        <>
          <h1 className="font-display text-2xl font-bold">Couldn&apos;t connect</h1>
          <p className="mt-2 text-destructive">{error}</p>
        </>
      ) : (
        // manual (copy/paste fallback)
        <>
          <h1 className="font-display text-2xl font-bold">Almost there</h1>
          <p className="mt-2 text-muted-foreground">
            Copy this code, then click the Aptly extension icon in your Chrome toolbar and paste it
            into the &quot;Have a connection code?&quot; field.
          </p>
          <div className="mt-4 flex w-full items-center gap-2">
            <code className="flex-1 select-all break-all rounded-md border border-border bg-card p-3 text-left text-xs">
              {token}
            </code>
            <Button variant="outline" onClick={() => void copy()}>
              {copied ? "Copied!" : "Copy"}
            </Button>
          </div>
          <p className="mt-3 text-xs text-muted-foreground">
            You can revoke this device anytime from Profile → Connected devices.
          </p>
        </>
      )}
    </div>
  );
}
