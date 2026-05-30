"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth-context";
import { useOpenLogin } from "@/lib/use-login-modal";
import { createExtensionSession } from "@/lib/api";

/**
 * Extension connect bridge. Reached when the user clicks "Sign in to Aptly" in
 * the extension popup (the popup opens this URL in a tab).
 *
 * Flow:
 *   1. Require an aptly.fyi session (cookie). Logged out → login modal.
 *   2. Mint a per-device extension bearer token (cookie-authed).
 *   3. Hand the token to the extension. Primary path: the extension passes its
 *      id via `?ext={id}` and we `chrome.runtime.sendMessage({id}, …)`
 *      (the page origin is in the extension's `externally_connectable`).
 *      Fallback: redirect to the extension's `connected.html#token=…` when an
 *      id is present but messaging isn't available.
 *
 * The token is shown to the extension exactly once here; the backend keeps
 * only its hash.
 */

type Phase = "checking" | "need-login" | "minting" | "done" | "manual" | "error";

declare global {
  interface Window {
    chrome?: {
      runtime?: {
        sendMessage?: (
          id: string,
          msg: unknown,
          cb?: (resp: unknown) => void,
        ) => void;
        lastError?: { message?: string };
      };
    };
  }
}

export default function ExtensionConnectPage() {
  const { status } = useAuth();
  const openLogin = useOpenLogin();
  const [phase, setPhase] = useState<Phase>("checking");
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (status === "loading") return;
    if (status === "unauthenticated") {
      setPhase("need-login");
      return;
    }
    // Authenticated → mint + hand off.
    let cancelled = false;
    (async () => {
      setPhase("minting");
      try {
        const device =
          typeof navigator !== "undefined" ? navigator.userAgent.slice(0, 80) : "Browser";
        const { token: t } = await createExtensionSession(device);
        if (cancelled) return;
        setToken(t);

        const params = new URLSearchParams(window.location.search);
        const extId = params.get("ext");
        const chromeRt = window.chrome?.runtime;

        if (extId && chromeRt?.sendMessage) {
          chromeRt.sendMessage(extId, { type: "APTLY_CONNECT", token: t }, () => {
            // Either way we land on a success screen; the extension stores it.
            setPhase("done");
          });
          // Give the callback a beat; if the extension isn't reachable, the
          // user still has the manual fallback below.
          setTimeout(() => !cancelled && setPhase((p) => (p === "minting" ? "done" : p)), 800);
          return;
        }
        if (extId) {
          // Messaging unavailable → fragment redirect into the extension page.
          window.location.href = `chrome-extension://${extId}/src/popup/connected.html#token=${encodeURIComponent(
            t,
          )}`;
          return;
        }
        // No extension id → show the token for manual paste (dev fallback).
        setPhase("manual");
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Couldn't connect");
          setPhase("error");
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [status]);

  return (
    <div className="container flex min-h-[60vh] max-w-md flex-col items-center justify-center py-16 text-center">
      {phase === "checking" || phase === "minting" ? (
        <p className="text-muted-foreground">Connecting your extension…</p>
      ) : phase === "need-login" ? (
        <>
          <h1 className="font-display text-2xl font-bold">Sign in to connect</h1>
          <p className="mt-2 text-muted-foreground">
            Sign in to aptly.fyi, then we&apos;ll link the Aptly browser extension to your account.
          </p>
          <Button className="mt-6" onClick={() => openLogin(window.location.pathname + window.location.search)}>
            Sign in
          </Button>
        </>
      ) : phase === "done" ? (
        <>
          <h1 className="font-display text-2xl font-bold">You&apos;re connected</h1>
          <p className="mt-2 text-muted-foreground">
            You can close this tab and use Aptly from your browser toolbar.
          </p>
        </>
      ) : phase === "manual" ? (
        <>
          <h1 className="font-display text-2xl font-bold">Almost there</h1>
          <p className="mt-2 text-muted-foreground">
            Paste this one-time code into the extension popup to finish connecting. It links this
            browser to your account.
          </p>
          <code className="mt-4 block w-full break-all rounded-md border border-border bg-card p-3 text-xs">
            {token}
          </code>
          <p className="mt-3 text-xs text-muted-foreground">
            You can revoke this device anytime from Profile → Connected devices.
          </p>
        </>
      ) : (
        <>
          <h1 className="font-display text-2xl font-bold">Couldn&apos;t connect</h1>
          <p className="mt-2 text-destructive">{error}</p>
        </>
      )}
    </div>
  );
}
