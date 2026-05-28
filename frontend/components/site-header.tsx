"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { Menu, X } from "lucide-react";

import { BrandMark } from "@/components/brand-mark";
import { SettingsMenu } from "@/components/settings-menu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";

/**
 * Top navigation for the app.
 *
 * Two modes:
 *
 *   * Logged out — minimal header with the brand + a Sign In CTA.
 *     The landing page handles its own marketing surface; we don't
 *     overload the chrome.
 *   * Logged in — brand, primary nav (six destinations), and a
 *     top-right SettingsMenu (avatar dropdown). The Admin link is
 *     surfaced INSIDE the settings menu — not in the primary nav —
 *     so it doesn't clutter the bar for the operator's day-to-day
 *     and stays out of sight for non-admins entirely.
 *
 * Active-state highlighting: each nav link compares the current
 * pathname against its `href`; a match (or a prefix match for nested
 * routes like `/jobs/123`) flips it into the highlighted style.
 *
 * Mobile: the desktop row of links collapses to a hamburger that
 * toggles a slide-down sheet with every link + the settings items.
 * Settings dropdown stays available on both layouts via SettingsMenu.
 */

type NavItem = { href: string; label: string };

const APP_NAV: NavItem[] = [
  { href: "/jobs", label: "Jobs" },
  { href: "/applications", label: "Application Tracker" },
  { href: "/interview-prep", label: "Interview Prep" },
  { href: "/ats", label: "ATS" },
  { href: "/email-finder", label: "Email Finder" },
  { href: "/support", label: "Support" },
];

export function SiteHeader() {
  const { user, loading } = useAuth();
  const pathname = usePathname() || "/";
  const [mobileOpen, setMobileOpen] = useState(false);

  // Close the mobile sheet on route change so a tap navigates AND
  // dismisses the overlay in one motion.
  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const isActive = (href: string): boolean => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/85 backdrop-blur-md supports-[backdrop-filter]:bg-background/65">
      <div className="container flex h-14 items-center gap-4 sm:h-16">
        {/* Brand → home. `/` server-side-redirects signed-in users
            to /profile or /jobs based on profile_saved; signed-out
            users see the landing page. Consistent "go home"
            semantics from anywhere in the app. */}
        <Link
          href="/"
          aria-label="Aptly home"
          className="flex items-center gap-2 font-semibold tracking-tight focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background rounded-md"
        >
          <BrandMark />
          <span className="font-display text-base">Aptly</span>
        </Link>

        {!loading && user && (
          <nav
            aria-label="Primary"
            className="ml-2 hidden flex-1 items-center gap-1 lg:flex"
          >
            {APP_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive(item.href) ? "page" : undefined}
                className={cn(
                  "relative rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive(item.href)
                    ? "text-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                {item.label}
                {isActive(item.href) && (
                  <span
                    aria-hidden="true"
                    className="absolute inset-x-2 -bottom-[7px] h-[2px] rounded-full bg-primary sm:-bottom-[9px]"
                  />
                )}
              </Link>
            ))}
          </nav>
        )}

        <div className="ml-auto flex items-center gap-2">
          {!loading && user && (
            <>
              <button
                type="button"
                aria-label={mobileOpen ? "Close menu" : "Open menu"}
                aria-expanded={mobileOpen}
                aria-controls="mobile-nav-sheet"
                onClick={() => setMobileOpen((o) => !o)}
                className="rounded-md border border-border/70 p-1.5 text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:hidden"
              >
                {mobileOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
              </button>
              <SettingsMenu />
            </>
          )}
          {!loading && !user && (
            <Button asChild size="sm" className="font-medium">
              <Link href="/sign-in">Sign in</Link>
            </Button>
          )}
        </div>
      </div>

      {/* Mobile slide-down sheet. Renders the same nav links as the
          desktop row + the settings items for thumb-friendly tap
          targets. The SettingsMenu stays available in the top-right
          on this size too, so users have either path. */}
      {!loading && user && mobileOpen && (
        <div
          id="mobile-nav-sheet"
          className="border-t border-border/60 bg-background lg:hidden"
        >
          <nav aria-label="Primary (mobile)" className="container space-y-1 py-3">
            {APP_NAV.map((item) => (
              <Link
                key={item.href}
                href={item.href}
                aria-current={isActive(item.href) ? "page" : undefined}
                className={cn(
                  "block rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive(item.href)
                    ? "bg-secondary text-foreground"
                    : "text-muted-foreground hover:bg-secondary hover:text-foreground",
                )}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </div>
      )}
    </header>
  );
}
