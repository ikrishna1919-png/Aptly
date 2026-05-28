import { ComingSoon } from "@/components/coming-soon";

// Public page — anyone can read what Interview Prep will do. (The feature
// itself is still coming soon.)
export default function InterviewPrepPage() {
  return (
    <ComingSoon
      eyebrow="Interview Prep"
      title="Role-specific prep, with a sponsorship lens."
      blurb="Generic interview-prep sites don't know your target company or your visa situation. Aptly's prep will: pull the most-asked questions for the role you're applying to, surface the public signal on how that company has handled sponsored hires before, and walk you through the questions YOU should be asking them."
      bullets={[
        "Role-specific question bank, pulled from public interview-experience reports.",
        "Per-company sponsorship signals already in the job feed, surfaced in the prep view.",
        "A worksheet of questions tailored to international candidates (start date, relocation, OPT/CPT handling).",
        "Mock-interview drills with feedback on filler / clarity / structure.",
      ]}
    />
  );
}
