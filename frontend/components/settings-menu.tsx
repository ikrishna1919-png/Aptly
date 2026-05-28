"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState, type MouseEvent } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ChevronDown,
  LogOut,
  LogIn,
  Globe2,
  Info,
  Mail,
  ShieldCheck,
  Settings as SettingsIcon,
  User as UserIcon,
  CreditCard,
  type LucideIcon,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";
import { useOpenLogin } from "@/lib/use-login-modal";

/**
 * Top-right account dropdown — now rendered for EVERYONE, not just
 * signed-in users (the nav surface is always visible).
 *
 *   * Signed in → avatar with initials, name/email header, and the
 *     full settings list incl. Sign out (and Admin when allowed).
 *   * Signed out → a generic "Account" trigger. Gated items
 *     (Profile, Subscription, Language, Contact us) open the login
 *     modal with their destination stashed as `?next=`; About us
 *     (public) navigates normally. The footer becomes "Sign in".
 *
 * Motion + interaction polish is unchanged: AnimatePresence open/close,
 * chevron rotate, click-outside + Escape, active-item highlight.
 */

type Item = {
  href: string;
  icon: LucideIcon;
  label: string;
  /** Requires auth — intercepted to open the login modal when the
   * visitor is signed out. */
  gated: boolean;
};

const ITEMS: Item[] = [
  { href: "/profile", icon: UserIcon, label: "Profile", gated: true },
  { href: "/settings/subscription", icon: CreditCard, label: "Subscription", gated: true },
  { href: "/settings/language", icon: Globe2, label: "Language", gated: true },
  { href: "/settings/contact", icon: Mail, label: "Contact us", gated: true },
  { href: "/about", icon: Info, label: "About us", gated: false },
];

export function SettingsMenu() {
  const { user, status, signOut } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const openLogin = useOpenLogin();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: globalThis.MouseEvent) {
      const node = containerRef.current;
      if (node && !node.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClickOutside);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClickOutside);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  useEffect(() => {
    setOpen(false);
  }, [pathname]);

  const signedIn = status === "authenticated" && !!user;
  const initials = user ? computeInitials(user.name, user.email) : null;

  const handleItemClick =
    (item: Item) => (e: MouseEvent<HTMLAnchorElement>) => {
      // Intercept gated items ONLY when the user is DEFINITIVELY signed
      // out (a real 401). During "loading"/"error" we let the click
      // through and trust the cookie + server — never pop the modal for a
      // signed-in user whose `/me` is slow or errored.
      if (item.gated && status === "unauthenticated") {
        e.preventDefault();
        setOpen(false);
        openLogin(item.href);
        return;
      }
      setOpen(false);
    };

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={signedIn ? "Open account menu" : "Open menu"}
        className={cn(
          "group flex items-center gap-2 rounded-full border border-border/70 bg-card py-1 pl-1 pr-2.5 transition-all duration-base",
          "hover:border-primary/30 hover:bg-primary-soft hover:shadow-card",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background",
          open && "border-primary/40 bg-primary-soft shadow-card",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "flex h-7 w-7 items-center justify-center rounded-full transition-transform duration-base",
            signedIn
              ? "bg-primary text-[11px] font-semibold text-primary-foreground shadow-sm"
              : "bg-secondary text-muted-foreground",
            open && "scale-105",
          )}
        >
          {signedIn ? initials : <SettingsIcon className="h-4 w-4" aria-hidden />}
        </span>
        <span className="hidden text-xs font-medium text-foreground sm:inline">
          {signedIn ? user!.name?.split(" ")[0] || user!.email.split("@")[0] : "Account"}
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 text-muted-foreground transition-transform duration-base",
            open && "rotate-180 text-primary",
          )}
          aria-hidden
        />
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6, scale: 0.97 }}
            transition={{ duration: 0.18, ease: [0.22, 1, 0.36, 1] }}
            role="menu"
            aria-label="Account"
            className="absolute right-0 z-50 mt-2 w-64 origin-top-right overflow-hidden rounded-xl border border-border bg-popover shadow-elevated"
          >
            <div className="border-b border-border/60 bg-primary-soft/40 px-4 py-3">
              {signedIn ? (
                <>
                  <p className="truncate text-sm font-semibold text-foreground">
                    {user!.name || user!.email.split("@")[0]}
                  </p>
                  <p className="truncate text-xs text-muted-foreground">{user!.email}</p>
                </>
              ) : (
                <>
                  <p className="text-sm font-semibold text-foreground">
                    Not signed in
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Sign in to unlock your jobs, profile, and settings.
                  </p>
                </>
              )}
            </div>
            <ul className="py-1.5">
              {ITEMS.map((item) => (
                <MenuItem
                  key={item.href}
                  href={item.href}
                  icon={item.icon}
                  label={item.label}
                  active={
                    item.href === "/about"
                      ? pathname === "/about"
                      : pathname === item.href
                  }
                  onClick={handleItemClick(item)}
                />
              ))}
              {signedIn && user!.is_admin && (
                <MenuItem
                  href="/admin"
                  icon={ShieldCheck}
                  label="Admin"
                  active={pathname?.startsWith("/admin") ?? false}
                  onClick={() => setOpen(false)}
                />
              )}
            </ul>
            <div className="border-t border-border/60 py-1.5">
              {signedIn ? (
                <button
                  type="button"
                  role="menuitem"
                  onClick={async () => {
                    setOpen(false);
                    try {
                      await signOut();
                    } catch {
                      openLogin();
                    }
                  }}
                  className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm font-medium text-foreground transition-colors duration-fast hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none"
                >
                  <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden />
                  Sign out
                </button>
              ) : (
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setOpen(false);
                    openLogin();
                  }}
                  className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm font-medium text-primary transition-colors duration-fast hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none"
                >
                  <LogIn className="h-4 w-4" aria-hidden />
                  Sign in
                </button>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function MenuItem({
  href,
  icon: Icon,
  label,
  active,
  onClick,
}: {
  href: string;
  icon: LucideIcon;
  label: string;
  active: boolean;
  onClick?: (e: MouseEvent<HTMLAnchorElement>) => void;
}) {
  return (
    <li>
      <Link
        href={href}
        role="menuitem"
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        className={cn(
          "flex items-center gap-3 px-4 py-2 text-sm font-medium transition-colors duration-fast",
          active
            ? "bg-primary-soft text-primary-soft-foreground"
            : "text-foreground hover:bg-secondary",
          "focus-visible:bg-secondary focus-visible:outline-none",
        )}
      >
        <Icon
          className={cn(
            "h-4 w-4 transition-colors duration-fast",
            active ? "text-primary" : "text-muted-foreground",
          )}
          aria-hidden
        />
        {label}
      </Link>
    </li>
  );
}

function computeInitials(name: string | null, email: string): string {
  if (name) {
    const parts = name.trim().split(/\s+/).filter(Boolean);
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
  }
  return email.slice(0, 2).toUpperCase();
}
