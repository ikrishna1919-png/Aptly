"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type MouseEvent } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  Menu,
  X,
  Briefcase,
  ClipboardList,
  GraduationCap,
  Search,
  Mailbox,
  LifeBuoy,
  type LucideIcon,
} from "lucide-react";

import { Logo } from "@/components/logo";
import { SettingsMenu } from "@/components/settings-menu";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";
import { useOpenLogin } from "@/lib/use-login-modal";
import { isGatedPath } from "@/lib/routes";

/**
 * Top navigation for the app.
 *
 * The primary nav + the Settings submenu now render UNCONDITIONALLY,
 * regardless of auth state — the app's surface is always visible. What
 * changes by auth state is what a click does:
 *
 *   * Logged in → links navigate normally.
 *   * Logged out + a gated destination → the click is intercepted and
 *     opens the login modal (`?login=1`) with the destination stashed
 *     as `?next=`, so OAuth returns the user there.
 *   * Public destinations (e.g. Support) always navigate.
 *
 * A logged-out visitor also gets a prominent "Sign in" button; a
 * logged-in visitor gets the avatar/SettingsMenu instead.
 *
 * Design system hooks:
 *   * Brand lockup always links home (icon-only on the tightest
 *     phones, full lockup once there's room).
 *   * Active-state highlighting: a thin primary rule under the live
 *     desktop nav item (shared `layoutId` so it slides between items).
 *   * Mobile sheet animates open/closed via AnimatePresence.
 */

type NavItem = { href: string; label: string; icon: LucideIcon; gated: boolean };

// `gated` is derived from the single source of truth in `lib/routes`
// so the header and the middleware can never drift apart. Support is
// a public page, so it always navigates.
const APP_NAV: NavItem[] = (
  [
    { href: "/jobs", label: "Jobs", icon: Briefcase },
    { href: "/applications", label: "Application Tracker", icon: ClipboardList },
    { href: "/interview-prep", label: "Interview Prep", icon: GraduationCap },
    { href: "/ats", label: "ATS", icon: Search },
    { href: "/email-finder", label: "Email Finder", icon: Mailbox },
    { href: "/support", label: "Support", icon: LifeBuoy },
  ] as const
).map((item) => ({ ...item, gated: isGatedPath(item.href) }));

export function SiteHeader() {
  const { user, loading } = useAuth();
  const pathname = usePathname() || "/";
  const openLogin = useOpenLogin();
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  const isActive = (href: string): boolean => {
    if (href === "/") return pathname === "/";
    return pathname === href || pathname.startsWith(`${href}/`);
  };

  // When logged out, intercept clicks on gated nav items: keep the
  // user on the page, open the login modal, and remember where they
  // were trying to go. Logged-in users (and public links) navigate
  // normally. While auth is still loading we let the click through —
  // the route guard / client gate will sort it out.
  const handleNavClick =
    (item: NavItem) => (e: MouseEvent<HTMLAnchorElement>) => {
      if (item.gated && !loading && !user) {
        e.preventDefault();
        openLogin(item.href);
      }
    };

  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/85 backdrop-blur-md supports-[backdrop-filter]:bg-background/65">
      <div className="container flex h-14 items-center gap-4 sm:h-16">
        <Link
          href="/"
          aria-label="Aptly home"
          className="group flex items-center rounded-md transition-opacity duration-base hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        >
          {/* Full lockup once there's room; icon alone on the
              tightest mobile widths so the brand never crowds the
              nav controls. */}
          <Logo
            wordmark={false}
            markClassName="h-8 w-8 transition-transform duration-base group-hover:scale-[1.04] xs:hidden"
          />
          <Logo className="hidden xs:inline-flex [&_svg]:transition-transform [&_svg]:duration-base group-hover:[&_svg]:scale-[1.04]" />
        </Link>

        <nav
          aria-label="Primary"
          className="ml-2 hidden flex-1 items-center gap-0.5 lg:flex"
        >
          {APP_NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              onClick={handleNavClick(item)}
              aria-current={isActive(item.href) ? "page" : undefined}
              className={cn(
                "relative rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-base focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isActive(item.href)
                  ? "text-foreground"
                  : "text-muted-foreground hover:bg-secondary hover:text-foreground",
              )}
            >
              {item.label}
              {isActive(item.href) && (
                <motion.span
                  layoutId="nav-active-rule"
                  aria-hidden="true"
                  transition={{ type: "spring", stiffness: 400, damping: 32 }}
                  className="absolute inset-x-2 -bottom-[7px] h-[2px] rounded-full bg-primary sm:-bottom-[9px]"
                />
              )}
            </Link>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <button
            type="button"
            aria-label={mobileOpen ? "Close menu" : "Open menu"}
            aria-expanded={mobileOpen}
            aria-controls="mobile-nav-sheet"
            onClick={() => setMobileOpen((o) => !o)}
            className="rounded-md border border-border/70 bg-card p-1.5 text-muted-foreground transition-all duration-base hover:border-primary/30 hover:bg-primary-soft hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:hidden"
          >
            {mobileOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
          </button>

          {!loading && !user && (
            <Button
              size="sm"
              className="font-semibold"
              onClick={() => openLogin()}
            >
              Sign in
            </Button>
          )}

          <SettingsMenu />
        </div>
      </div>

      <AnimatePresence>
        {mobileOpen && (
          <motion.div
            id="mobile-nav-sheet"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
            className="border-t border-border/60 bg-background lg:hidden"
          >
            <nav aria-label="Primary (mobile)" className="container space-y-1 py-3">
              {APP_NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  onClick={(e) => {
                    handleNavClick(item)(e);
                    setMobileOpen(false);
                  }}
                  aria-current={isActive(item.href) ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors duration-fast",
                    isActive(item.href)
                      ? "bg-primary-soft text-primary-soft-foreground"
                      : "text-foreground hover:bg-secondary",
                  )}
                >
                  <item.icon
                    className={cn(
                      "h-4 w-4",
                      isActive(item.href) ? "text-primary" : "text-muted-foreground",
                    )}
                    aria-hidden
                  />
                  {item.label}
                </Link>
              ))}
            </nav>
          </motion.div>
        )}
      </AnimatePresence>
    </header>
  );
}
