"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
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
 * Click the trigger to open a small menu with the account-scoped
 * actions: Profile, Subscription, Language, Contact Us, About Us,
 * (Admin if the user's email is in the admin allowlist), Sign Out.
 *
 * Implementation notes:
 *   * Native click-outside + Escape-to-close handled inline so we
 *     don't pull in a Radix dropdown for one menu.
 *   * Each item closes the menu on click via `setOpen(false)`.
 *   * The trigger shows initials (consistent with the profile-page
 *     IdentityCard avatar) so the same identity glyph reads the
 *     same everywhere.
 *   * `aria-expanded` + `aria-haspopup` for screen-readers.
 */
export function SettingsMenu() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  // Close on outside click + Escape.
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

  // Close on route change so the menu doesn't stay open after a click.
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
        aria-label="Open settings menu"
        className={cn(
          "group flex items-center gap-2 rounded-full border border-border/70 bg-background/80 py-1 pl-1 pr-2.5 transition-colors hover:border-border hover:bg-secondary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          open && "border-primary/60 bg-secondary",
        )}
      >
        <span
          aria-hidden="true"
          className="flex h-7 w-7 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-accent-foreground"
        >
          {initials}
        </span>
        <span className="hidden text-xs font-medium text-foreground sm:inline">
          {user.name?.split(" ")[0] || user.email.split("@")[0]}
        </span>
        <ChevronDown
          className={cn(
            "h-3.5 w-3.5 text-muted-foreground transition-transform",
            open && "rotate-180",
          )}
          aria-hidden
        />
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Account"
          className="absolute right-0 z-50 mt-2 w-60 origin-top-right overflow-hidden rounded-xl border border-border/70 bg-card shadow-lg"
        >
          <div className="border-b border-border/60 px-4 py-3">
            <p className="truncate text-sm font-medium text-foreground">
              {user.name || user.email.split("@")[0]}
            </p>
            <p className="truncate text-xs text-muted-foreground">{user.email}</p>
          </div>
          <ul className="py-1">
            <MenuItem href="/profile" icon={UserIcon} label="Profile" />
            <MenuItem
              href="/settings/subscription"
              icon={CreditCard}
              label="Subscription"
            />
            <MenuItem
              href="/settings/language"
              icon={Globe2}
              label="Language"
            />
            <MenuItem href="/settings/contact" icon={Mail} label="Contact us" />
            <MenuItem href="/about" icon={Info} label="About us" />
            {user.is_admin && (
              <MenuItem href="/admin" icon={ShieldCheck} label="Admin" />
            )}
          </ul>
          <div className="border-t border-border/60 py-1">
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
              className="flex w-full items-center gap-3 px-4 py-2 text-left text-sm text-foreground hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none"
            >
              <LogOut className="h-4 w-4 text-muted-foreground" aria-hidden />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function MenuItem({
  href,
  icon: Icon,
  label,
}: {
  href: string;
  icon: typeof UserIcon;
  label: string;
}) {
  return (
    <li>
      <Link
        href={href}
        role="menuitem"
        className="flex items-center gap-3 px-4 py-2 text-sm text-foreground hover:bg-secondary focus-visible:bg-secondary focus-visible:outline-none"
      >
        <Icon className="h-4 w-4 text-muted-foreground" aria-hidden />
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
