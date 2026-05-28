"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import {
  ChevronDown,
  LogOut,
  Globe2,
  Info,
  Mail,
  ShieldCheck,
  User as UserIcon,
  CreditCard,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/auth-context";

/**
 * Top-right avatar dropdown for logged-in users.
 *
 * Lives in the SiteHeader, opens to: Profile, Subscription,
 * Language, Contact us, About us, Admin (only when
 * `user.is_admin`), Sign out. The Admin entry surfaces here
 * deliberately — keeps the main nav uncluttered AND keeps it out
 * of sight entirely for non-admin accounts.
 *
 * Motion + interaction polish:
 *   * Open/close uses AnimatePresence with a quick (180ms) ease-
 *     out spring. The trigger's chevron rotates 180° on open.
 *   * Active-state highlight: the menu item whose `href` matches
 *     the current path lights up in primary-soft so the user can
 *     see "I'm already on Profile" without parsing the page.
 *   * Click-outside + Escape close. Route change closes too.
 *   * `aria-haspopup` / `aria-expanded` for screen readers.
 */
export function SettingsMenu() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
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

  if (!user) return null;

  const initials = computeInitials(user.name, user.email);

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="Open account menu"
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
            "flex h-7 w-7 items-center justify-center rounded-full bg-primary text-[11px] font-semibold text-primary-foreground shadow-sm transition-transform duration-base",
            open && "scale-105",
          )}
        >
          {initials}
        </span>
        <span className="hidden text-xs font-medium text-foreground sm:inline">
          {user.name?.split(" ")[0] || user.email.split("@")[0]}
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
              <p className="truncate text-sm font-semibold text-foreground">
                {user.name || user.email.split("@")[0]}
              </p>
              <p className="truncate text-xs text-muted-foreground">{user.email}</p>
            </div>
            <ul className="py-1.5">
              <MenuItem
                href="/profile"
                icon={UserIcon}
                label="Profile"
                active={pathname === "/profile"}
              />
              <MenuItem
                href="/settings/subscription"
                icon={CreditCard}
                label="Subscription"
                active={pathname === "/settings/subscription"}
              />
              <MenuItem
                href="/settings/language"
                icon={Globe2}
                label="Language"
                active={pathname === "/settings/language"}
              />
              <MenuItem
                href="/settings/contact"
                icon={Mail}
                label="Contact us"
                active={pathname === "/settings/contact"}
              />
              <MenuItem
                href="/about"
                icon={Info}
                label="About us"
                active={pathname === "/about"}
              />
              {user.is_admin && (
                <MenuItem
                  href="/admin"
                  icon={ShieldCheck}
                  label="Admin"
                  active={pathname?.startsWith("/admin") ?? false}
                />
              )}
            </ul>
            <div className="border-t border-border/60 py-1.5">
              <button
                type="button"
                role="menuitem"
                onClick={async () => {
                  setOpen(false);
                  try {
                    await signOut();
                  } catch {
                    router.push("/sign-in");
                  }
                }}
                className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm font-medium text-foreground transition-colors duration-fast hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none"
              >
                <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden />
                Sign out
              </button>
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
}: {
  href: string;
  icon: typeof UserIcon;
  label: string;
  active: boolean;
}) {
  return (
    <li>
      <Link
        href={href}
        role="menuitem"
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
