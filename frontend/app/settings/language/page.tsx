"use client";

import { useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { RequireAuth } from "@/lib/auth-context";

/** Languages we plan to support. English is the only one wired up
 * today; the rest list with a `disabled` flag so the picker is real
 * UI but the alternatives clearly read as future work. */
const LANGUAGES: { code: string; label: string; available: boolean }[] = [
  { code: "en", label: "English", available: true },
  { code: "es", label: "Español", available: false },
  { code: "fr", label: "Français", available: false },
  { code: "de", label: "Deutsch", available: false },
  { code: "zh", label: "中文 (简体)", available: false },
  { code: "hi", label: "हिन्दी", available: false },
];

const STORAGE_KEY = "aptly.language";

export default function LanguageSettingsPage() {
  return (
    <RequireAuth>
      <LanguageInner />
    </RequireAuth>
  );
}

function LanguageInner() {
  const [selected, setSelected] = useState<string>("en");

  useEffect(() => {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) setSelected(stored);
  }, []);

  function choose(code: string) {
    setSelected(code);
    localStorage.setItem(STORAGE_KEY, code);
  }

  return (
    <main className="container max-w-2xl space-y-8 py-12 sm:py-16">
      <header className="space-y-3">
        <Badge
          variant="outline"
          className="border-primary/30 bg-primary/5 text-xs font-medium uppercase tracking-[0.16em] text-primary"
        >
          Settings · Language
        </Badge>
        <h1 className="font-display text-3xl font-medium tracking-tight text-foreground sm:text-4xl">
          Language
        </h1>
        <p className="max-w-xl text-base leading-relaxed text-muted-foreground">
          The Aptly interface is English-only today. Other languages are on
          the roadmap — your selection here is remembered so we can switch
          you over the moment translations land.
        </p>
      </header>

      <Card className="border-border/70 shadow-sm">
        <CardHeader>
          <CardTitle className="font-display text-lg font-medium tracking-tight">
            Choose your language
          </CardTitle>
          <CardDescription>
            Stored in this browser (localStorage) until per-user language
            preference syncs to your account.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <fieldset className="space-y-2">
            <legend className="sr-only">Language</legend>
            {LANGUAGES.map((lang) => {
              const isSelected = selected === lang.code;
              return (
                <label
                  key={lang.code}
                  className={`flex cursor-pointer items-center justify-between rounded-lg border px-4 py-3 transition-colors ${
                    isSelected
                      ? "border-primary bg-primary/5"
                      : "border-border/70 hover:border-border"
                  } ${lang.available ? "" : "cursor-not-allowed opacity-60"}`}
                >
                  <span className="flex items-center gap-3">
                    <input
                      type="radio"
                      name="language"
                      value={lang.code}
                      checked={isSelected}
                      disabled={!lang.available}
                      onChange={() => lang.available && choose(lang.code)}
                      className="h-4 w-4 accent-primary"
                    />
                    <span className="text-sm font-medium text-foreground">
                      {lang.label}
                    </span>
                  </span>
                  {!lang.available && (
                    <span className="text-[11px] font-medium uppercase tracking-[0.12em] text-muted-foreground">
                      Coming soon
                    </span>
                  )}
                </label>
              );
            })}
          </fieldset>
        </CardContent>
      </Card>
    </main>
  );
}
