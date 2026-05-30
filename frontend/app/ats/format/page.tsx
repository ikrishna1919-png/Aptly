import { FormatChooser } from "@/components/ats/format-chooser";

const RESUME_FORMATS = [
  { id: "modern", name: "Modern", blurb: "Clean sans, subtle blue accents" },
  { id: "classic", name: "Classic", blurb: "Traditional serif, two-line blocks" },
  { id: "minimal", name: "Minimal", blurb: "Generous whitespace, light weight" },
  { id: "plain", name: "Plain", blurb: "Max ATS compat, no styling" },
];

export default function ChooseResumeFormatPage() {
  return (
    <FormatChooser kind="resume" title="Choose your default resume format" formats={RESUME_FORMATS} />
  );
}
