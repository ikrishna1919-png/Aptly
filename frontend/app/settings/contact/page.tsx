"use client";

import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { RequireAuth, useAuth } from "@/lib/auth-context";

const SUPPORT_EMAIL = "hello@aptly.fyi";

export default function ContactSettingsPage() {
  return (
    <RequireAuth>
      <ContactInner />
    </RequireAuth>
  );
}

function ContactInner() {
  const { user } = useAuth();
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");

  const mailto = `mailto:${SUPPORT_EMAIL}?subject=${encodeURIComponent(
    subject || "Aptly support",
  )}&body=${encodeURIComponent(
    [
      body.trim(),
      "",
      "---",
      user?.email ? `From: ${user.email}` : "",
      typeof window !== "undefined" ? `Browser: ${navigator.userAgent}` : "",
    ]
      .filter(Boolean)
      .join("\n"),
  )}`;

  return (
    <main className="container max-w-2xl space-y-8 py-12 sm:py-16">
      <header className="space-y-3">
        <Badge
          variant="outline"
          className="border-primary/30 bg-primary/5 text-xs font-medium uppercase tracking-[0.16em] text-primary"
        >
          Settings · Contact us
        </Badge>
        <h1 className="font-display text-3xl font-medium tracking-tight text-foreground sm:text-4xl">
          Contact us
        </h1>
        <p className="max-w-xl text-base leading-relaxed text-muted-foreground">
          Write your message below, then click <strong>Send via email</strong>.
          Your default mail client will open with a draft addressed to{" "}
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs">
            {SUPPORT_EMAIL}
          </code>{" "}
          — review and hit send.
        </p>
      </header>

      <Card className="border-border/70 shadow-sm">
        <CardHeader>
          <CardTitle className="font-display text-lg font-medium tracking-tight">
            Write to the team
          </CardTitle>
          <CardDescription>
            Mailto-only for now — we don&apos;t pipe form submissions through a
            third-party today. Once we have a real inbox-and-ticketing tool,
            this will post directly.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium text-foreground">Subject</span>
            <Input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="What's this about?"
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium text-foreground">Message</span>
            <Textarea
              rows={6}
              value={body}
              onChange={(e) => setBody(e.target.value)}
              placeholder="The bug, the feature you want, or the question."
            />
          </label>
          <div className="flex items-center justify-between gap-3 pt-1">
            <p className="text-xs text-muted-foreground">
              We attach your signed-in email + browser info to help us debug.
            </p>
            <Button asChild disabled={!body.trim()}>
              <a href={mailto}>Send via email</a>
            </Button>
          </div>
        </CardContent>
      </Card>
    </main>
  );
}
