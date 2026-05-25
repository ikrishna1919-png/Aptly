import Link from "next/link";

import { BrandMark } from "@/components/brand-mark";

const nav = [
  { href: "/", label: "Jobs" },
  { href: "/admin", label: "Admin" },
];

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border/60 bg-background/80 backdrop-blur-md supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-14 items-center gap-6">
        <Link
          href="/"
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

        <div className="ml-auto flex items-center gap-2">
          <span className="hidden sm:inline-flex items-center rounded-full border border-border/80 bg-secondary/60 px-2.5 py-0.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
            48h rolling
          </span>
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
