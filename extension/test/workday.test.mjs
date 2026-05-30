// Node test for the Workday custom-dropdown matcher (matchOptionByText). The
// Workday DOM itself can't be exercised here (no browser), but the *matching*
// logic that decides which [role="option"] a value selects is pure, so we lock
// it down against a tiny option stub — mirroring test/detection.test.mjs.
// Run: `node test/workday.test.mjs`.
import assert from "node:assert";
import { matchOptionByText } from "../src/content/shared.js";

let passed = 0;
function check(name, cond) {
  assert.ok(cond, name);
  passed++;
}

// Option stub: matchOptionByText only reads `.textContent`.
const opt = (textContent) => ({ textContent });
const options = (...labels) => labels.map(opt);

// ── Exact (normalized) matching ─────────────────────────────────────────────
{
  const os = options("Yes", "No");
  check("exact match returns that option", matchOptionByText(os, "Yes") === os[0]);
  check("exact match (second)", matchOptionByText(os, "No") === os[1]);
  check("case-insensitive exact", matchOptionByText(os, "yes") === os[0]);
  check(
    "whitespace-insensitive exact",
    matchOptionByText(options("United  States"), "united states") !== null,
  );
}

// Exact must win over a looser containment match regardless of option order.
{
  const os = options("No", "Not sure");
  check("exact 'No' beats contains 'Not sure'", matchOptionByText(os, "No") === os[0]);
  const reordered = options("Not sure", "No");
  check(
    "exact 'No' wins even when listed after 'Not sure'",
    matchOptionByText(reordered, "No") === reordered[1],
  );
}

// ── Loose containment (Workday labels carry longer/shorter forms) ───────────
{
  const os = options("United States of America", "United Kingdom");
  check(
    "value shorter than option label matches (US → 'United States of America')",
    matchOptionByText(os, "United States") === os[0],
  );
  const os2 = options("United States");
  check(
    "value longer than option label matches ('United States of America' → US)",
    matchOptionByText(os2, "United States of America") === os2[0],
  );
}

// ── No-match / guard cases ──────────────────────────────────────────────────
check("no match returns null", matchOptionByText(options("Alpha", "Beta"), "Gamma") === null);
check("empty value returns null", matchOptionByText(options("Alpha"), "") === null);
check("null value returns null", matchOptionByText(options("Alpha"), null) === null);
check("empty options returns null", matchOptionByText([], "Alpha") === null);
check(
  "1-char value does not loose-match a longer option",
  matchOptionByText(options("Australia"), "a") === null,
);

console.log(`workday.test: ${passed} assertions passed`);
