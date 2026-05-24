import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Aptly",
  description:
    "Aggregates real jobs, filters for what matters, and AI-tailors your resume per role.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background font-sans antialiased">{children}</body>
    </html>
  );
}
