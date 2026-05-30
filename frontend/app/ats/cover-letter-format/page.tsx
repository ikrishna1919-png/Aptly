import { FormatChooser } from "@/components/ats/format-chooser";

const COVER_FORMATS = [
  { id: "traditional", name: "Traditional", blurb: "Serif, classic business letter" },
  { id: "modern", name: "Modern", blurb: "Sans-serif, clean and current" },
  { id: "concise", name: "Concise", blurb: "Tight, to-the-point" },
];

export default function ChooseCoverFormatPage() {
  return (
    <FormatChooser
      kind="cover"
      title="Choose your default cover letter format"
      formats={COVER_FORMATS}
    />
  );
}
