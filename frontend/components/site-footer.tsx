import { BrandMark } from "@/components/brand-mark";

export function SiteFooter() {
  return (
    <footer className="border-t border-border/60 bg-background">
      <div className="container flex flex-col items-start gap-3 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2">
          <BrandMark className="h-5 w-5" />
          <span className="font-medium text-foreground">Aptly</span>
          <span className="text-muted-foreground">
            · Jobs aggregated from public ATS boards.
          </span>
        </div>
        <p className="text-xs text-muted-foreground">
          Greenhouse · Lever · Updated every 6 hours
        </p>
      </div>
    </footer>
  );
}
