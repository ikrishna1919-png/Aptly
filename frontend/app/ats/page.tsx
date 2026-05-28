import { ComingSoon } from "@/components/coming-soon";
import { RequireAuth } from "@/lib/auth-context";

export default function ATSPage() {
  return (
    <RequireAuth>
      <ComingSoon
        eyebrow="ATS Checker"
        title="See how a job will read your resume — before you apply."
        blurb="Most companies route applications through an Applicant Tracking System that ranks resumes against the job's keyword profile. Aptly's checker will score a candidate resume against any job in the feed, flag missing keywords, and suggest the lines where they'd fit truthfully."
        bullets={[
          "Per-job ATS score with a clear breakdown of matched, missing, and partial-credit keywords.",
          "Suggestions are tied to bullets in YOUR resume, not generated lines — never invents experience you don't have.",
          "Same scoring runs inside the existing tailoring flow so the tailored output already meets the bar.",
          "Side-by-side diff of the JD's must-haves vs. your resume's surface area.",
        ]}
      />
    </RequireAuth>
  );
}
