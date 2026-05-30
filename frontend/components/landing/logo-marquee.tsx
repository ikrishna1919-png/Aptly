"use client";

/**
 * Continuous right-to-left logo marquee.
 *
 * HONESTY: every company here appears in Aptly's ingestion seed
 * (`infra/company_seed.tsx` → the ATS tokens we aggregate from), so we're
 * not advertising employers whose jobs aren't in the feed. The live feed
 * couldn't be queried at build time; this list should be reconciled against
 * `select distinct company from jobs` and trimmed of anything not currently
 * present.
 *
 * RENDERING: these are monochrome WORDMARKS (styled company names), not brand
 * SVGs — the sandbox had no `simple-icons`/`react-icons` available and no
 * network to add one. `CompanyMark` is the single swap point: drop a real SVG
 * in here later and the marquee is unchanged.
 *
 * MOTION: a pure CSS keyframe translate (GPU-friendly) over a doubled list so
 * the loop seams cleanly; paused on hover and under reduced-motion. Edge
 * gradient mask so logos fade in/out rather than popping at the edges.
 */

const COMPANIES = [
  "Stripe",
  "Airbnb",
  "Coinbase",
  "Datadog",
  "DoorDash",
  "Dropbox",
  "Lyft",
  "Palantir",
  "Pinterest",
  "Reddit",
  "Robinhood",
  "Roblox",
  "Shopify",
  "Square",
  "Twilio",
  "Unity",
  "Affirm",
  "Asana",
];

function CompanyMark({ name }: { name: string }) {
  return (
    <span className="select-none whitespace-nowrap text-xl font-semibold tracking-tight text-muted-foreground/70 transition-colors duration-300 hover:text-foreground sm:text-2xl">
      {name}
    </span>
  );
}

export function LogoMarquee() {
  // Doubled so translateX(-50%) lands exactly on the seam.
  const loop = [...COMPANIES, ...COMPANIES];
  return (
    <div
      aria-label="Companies in the Aptly job feed"
      className="group relative w-full overflow-hidden"
      style={{
        maskImage:
          "linear-gradient(to right, transparent, black 8%, black 92%, transparent)",
        WebkitMaskImage:
          "linear-gradient(to right, transparent, black 8%, black 92%, transparent)",
      }}
    >
      <style>{`
        @keyframes aptly-marquee { from { transform: translateX(0); } to { transform: translateX(-50%); } }
        .aptly-marquee-track { animation: aptly-marquee 38s linear infinite; }
        .group:hover .aptly-marquee-track { animation-play-state: paused; }
        @media (prefers-reduced-motion: reduce) {
          .aptly-marquee-track { animation: none; transform: translateX(0); }
        }
      `}</style>
      <div className="aptly-marquee-track flex w-max items-center gap-12 pr-12">
        {loop.map((name, i) => (
          <CompanyMark key={`${name}-${i}`} name={name} />
        ))}
      </div>
    </div>
  );
}
