import { ComingSoon } from "@/components/coming-soon";

// Public page — anyone can read what the Email Finder will do. (The feature
// itself is still coming soon.)
export default function EmailFinderPage() {
  return (
    <ComingSoon
      eyebrow="Email Finder"
      title="Reach the person who can actually move your application."
      blurb="A targeted note to a recruiter or hiring manager beats another resume sitting in the ATS queue. The Email Finder will surface verified public contact info for the right person at each company in the feed — built carefully so it respects opt-outs and public-data norms, and never enables mass blasting."
      bullets={[
        "Per-company contact list of recruiters / hiring managers, sourced from public profiles only.",
        "One-click prefilled draft anchored to the job you're applying to — you review and send.",
        "Cooldown between outreach to the same contact so the workflow can't be turned into a blast tool.",
        "Honours public opt-out signals; never scrapes private directories or social-graph data.",
      ]}
    />
  );
}
