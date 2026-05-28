import { Compass } from "lucide-react";

/**
 * `/jobs` with no selection. Renders into the shell's right pane (desktop)
 * as a quiet empty state. On mobile, `/jobs` shows the list only and this
 * pane is hidden, so this is effectively a desktop affordance.
 */
export default function JobsIndexPage() {
  return (
    <div className="flex h-full items-center justify-center p-8">
      <div className="max-w-xs text-center">
        <span className="mx-auto flex h-12 w-12 items-center justify-center rounded-full bg-secondary text-muted-foreground">
          <Compass className="h-5 w-5" aria-hidden />
        </span>
        <p className="mt-4 text-sm font-medium text-foreground">
          Select a job to see details
        </p>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          Pick a posting from the list to view the description, sponsorship
          insights, and tailor your resume. Use ↑↓ to move through the list.
        </p>
      </div>
    </div>
  );
}
