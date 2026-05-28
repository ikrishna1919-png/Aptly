import { ComingSoon } from "@/components/coming-soon";
import { RequireAuth } from "@/lib/auth-context";
import { ApplicationTrackerPreview } from "./preview";

export default function ApplicationsPage() {
  return (
    <RequireAuth>
      <ComingSoon
        eyebrow="Application Tracker"
        title="Every application, every status, in one place."
        blurb="Right now most job-seekers track their pipeline in a spreadsheet that goes stale within a week. Aptly will keep it current by linking each saved job to its application status — so you can see what's pending, what needs a follow-up, and what's already a no, without leaving the app."
        bullets={[
          "Kanban or table view, by status: applied, screen, interviewing, offer, rejected.",
          "Per-application notes — recruiter name, screen date, follow-up dates.",
          "Auto-detect a stalled application after N days and prompt a follow-up.",
          "Export the whole tracker to CSV when you want a record outside Aptly.",
        ]}
        preview={<ApplicationTrackerPreview />}
      />
    </RequireAuth>
  );
}
