"use client";

import { motion, useReducedMotion } from "motion/react";

/**
 * Full-bleed animated gradient backdrop for a landing panel.
 *
 * Two overlapping soft radial blobs built ONLY from existing palette
 * tokens (`--primary`, `--accent`, `--highlight`, `--primary-soft`) at low
 * opacity, slowly rotating + breathing. `variant` shifts the blob
 * positions/hues so Panel 1 and Panel 2 read differently and the
 * panel-to-panel transition carries visual variety.
 *
 * Readability is non-negotiable: opacities stay in the 8–18% range and the
 * blobs sit behind a foreground that has its own solid text colors. Honors
 * `prefers-reduced-motion` by rendering the same blobs statically.
 */
export function GradientBackdrop({ variant }: { variant: "one" | "two" }) {
  const reduce = useReducedMotion();

  const blobs =
    variant === "one"
      ? [
          {
            // primary wash, top-right
            style: {
              background:
                "radial-gradient(circle at center, hsl(var(--primary) / 0.18), transparent 70%)",
              top: "-15%",
              right: "-10%",
              width: "55vw",
              height: "55vw",
            },
            spin: 48,
            scale: [1, 1.06, 1],
          },
          {
            // accent wash, bottom-left
            style: {
              background:
                "radial-gradient(circle at center, hsl(var(--accent) / 0.5), transparent 70%)",
              bottom: "-20%",
              left: "-12%",
              width: "50vw",
              height: "50vw",
            },
            spin: -60,
            scale: [1.04, 1, 1.04],
          },
        ]
      : [
          {
            // primary-soft wash, top-left (different corner than panel one)
            style: {
              background:
                "radial-gradient(circle at center, hsl(var(--primary) / 0.16), transparent 70%)",
              top: "-18%",
              left: "-8%",
              width: "52vw",
              height: "52vw",
            },
            spin: 54,
            scale: [1, 1.05, 1],
          },
          {
            // warm highlight hint, bottom-right (subtle hue variety)
            style: {
              background:
                "radial-gradient(circle at center, hsl(var(--highlight) / 0.1), transparent 70%)",
              bottom: "-22%",
              right: "-10%",
              width: "48vw",
              height: "48vw",
            },
            spin: -50,
            scale: [1.05, 1, 1.05],
          },
        ];

  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      {blobs.map((b, i) => (
        <motion.div
          key={i}
          className="absolute rounded-full blur-3xl"
          style={b.style}
          animate={
            reduce
              ? undefined
              : { rotate: [0, b.spin > 0 ? 360 : -360], scale: b.scale }
          }
          transition={
            reduce
              ? undefined
              : {
                  rotate: { duration: Math.abs(b.spin), repeat: Infinity, ease: "linear" },
                  scale: { duration: 10, repeat: Infinity, ease: "easeInOut" },
                }
          }
        />
      ))}
      {/* A whisper of grain keeps the gradients from banding on wide gamuts. */}
      <div
        className="absolute inset-0 opacity-[0.015] mix-blend-overlay"
        style={{
          backgroundImage:
            "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E\")",
        }}
      />
    </div>
  );
}
