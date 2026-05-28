import { BrandMark } from "@/components/brand-mark";
import { cn } from "@/lib/utils";

/**
 * Full Aptly logo lockup — the brand icon next to the "Aptly"
 * wordmark.
 *
 * The wordmark is live text in the site's display face (Fraunces,
 * loaded in `app/layout.tsx` as `--font-display`) rather than an
 * image, so it renders pixel-crisp at every size, inherits the
 * current text colour (works on light + dark surfaces), and stays
 * perfectly matched to the brand typography used everywhere else.
 *
 * `wordmark={false}` drops to the icon alone — used where space is
 * tight (the collapsed mobile nav). A standalone, self-contained
 * SVG version of this lockup (wordmark outlined) lives at
 * `public/assets/aptly-logo.svg` for off-site / favicon-adjacent
 * use.
 */
export function Logo({
  className,
  markClassName,
  wordmarkClassName,
  wordmark = true,
}: {
  className?: string;
  markClassName?: string;
  wordmarkClassName?: string;
  /** Show the "Aptly" wordmark beside the icon. Defaults to true. */
  wordmark?: boolean;
}) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <BrandMark className={cn("h-8 w-8", markClassName)} />
      {wordmark && (
        <span
          className={cn(
            "font-display text-[1.35rem] font-medium leading-none tracking-tight text-foreground",
            wordmarkClassName,
          )}
        >
          Aptly
        </span>
      )}
    </span>
  );
}
