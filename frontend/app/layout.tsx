import type { Metadata } from "next";
import { Fraunces, Plus_Jakarta_Sans } from "next/font/google";

import { SiteFooter } from "@/components/site-footer";
import { SiteHeader } from "@/components/site-header";
import { AuthProvider } from "@/lib/auth-context";
import { cn } from "@/lib/utils";

import "./globals.css";

// Display face for headlines + section titles. Fraunces is a
// contemporary serif with optical-size + soft variable axes — it
// reads modern but warm, which fits the landing page's "credible,
// reassuring, not hypey" tone for an audience navigating visa
// stress. Loaded with the variable suffix as a Tailwind CSS variable
// so we can target it explicitly via the `font-display` utility.
const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-display",
  display: "swap",
  axes: ["opsz", "SOFT"],
});

// Body face. Plus Jakarta Sans is a contemporary humanist sans with
// sharper, more characterful glyph shapes than Inter / Roboto / the
// system stack. Picked specifically to land outside the
// "generic AI-app aesthetic" the design brief rules out.
const body = Plus_Jakarta_Sans({
  subsets: ["latin"],
  variable: "--font-sans",
  display: "swap",
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: {
    default: "Aptly — jobs that actually sponsor visas",
    template: "%s · Aptly",
  },
  description:
    "Find the jobs that will actually sponsor your visa, and tailor your resume to land them — built for international students and H-1B candidates.",
  metadataBase: new URL("https://aptly.local"),
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={cn(
          "min-h-screen bg-background font-sans text-foreground antialiased",
          fraunces.variable,
          body.variable,
        )}
      >
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
        >
          Skip to main content
        </a>
        <AuthProvider>
          <div className="relative flex min-h-screen flex-col">
            <SiteHeader />
            <main id="main" className="flex-1">
              {children}
            </main>
            <SiteFooter />
          </div>
        </AuthProvider>
      </body>
    </html>
  );
}
