"use client";

import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";
import { useAuth } from "@/lib/auth-context";

// In-app nav for signed-in users — anchored to `/jobs`, the feed
// home (the bare `/` is the public landing now). Profile + admin are
// unchanged.
const nav = [
  { href: "/jobs", label: "Jobs" },
  { href: "/profile", label: "Profile" },
  { href: "/admin", label: "Admin" },
];

export function SiteHeader() {
  const { user, loading, signOut } = useAuth();
  // Logged-in users get sent into the app when they click the brand;
  // logged-out users get sent to the landing page so they can read
  // the marketing copy + sign-in CTA. `/` redirects authenticated
  // visitors server-side, so pointing at `/jobs` directly avoids a
  // redirect bounce when a signed-in user clicks the brand.
  const brandHref = user ? "/jobs" : "/";
  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-14 items-center gap-6">
        <Link
          href={brandHref}
          className="flex items-center gap-2 font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
        >
          <BrandMark />
          <span className="text-base">Aptly</span>
        </Link>

        <nav aria-label="Primary" className="hidden md:flex items-center gap-1">
          {nav.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {item.label}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          {/* Auth slot. `loading` renders nothing so the header
              doesn't flicker between states on first paint. */}
          {!loading && user && (
            <>
              <span
                className="hidden text-xs text-muted-foreground sm:inline"
                title={user.email}
              >
                {user.name || user.email}
              </span>
              <button
                type="button"
                onClick={() => void signOut()}
                className="rounded-md px-2 py-1 text-xs font-medium text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                Sign out
              </button>
            </>
          )}
          {!loading && !user && (
            <Link
              href="/sign-in"
              className="rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground hover:bg-primary/90"
            >
              Sign in
            </Link>
          )}
        </div>
      </div>

      {/* Mobile nav row */}
      <nav
        aria-label="Mobile primary"
        className="container flex items-center gap-1 pb-2 pt-0 md:hidden"
      >
        {nav.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className="rounded-md px-3 py-1.5 text-sm font-medium text-muted-foreground hover:bg-secondary hover:text-foreground"
          >
            {item.label}
          </Link>
        ))}
      </nav>
    </header>
  );
}
