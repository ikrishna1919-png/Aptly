// Node test for the compliance/EEO mapping (complianceKeyFor / complianceValue /
// setSelectByText). Pure functions over the profile + a tiny <select> stub — no
// browser needed. Run: `node test/compliance.test.mjs`.
import assert from "node:assert";

import {
  EEO_KEYS,
  complianceKeyFor,
  complianceValue,
  contactKeyFor,
  setSelectByText,
} from "../src/content/shared.js";

let passed = 0;
function check(name, cond) {
  assert.ok(cond, name);
  passed++;
}

// ── complianceKeyFor: maps standardized question wording → key ──────────────
check(
  "sponsorship question → requires_sponsorship",
  complianceKeyFor("Will you now or in the future require sponsorship for employment?") ===
    "requires_sponsorship",
);
check(
  "work-auth question → work_authorization",
  complianceKeyFor("Are you legally authorized to work in the United States?") ===
    "work_authorization",
);
check("veteran → veteran_status", complianceKeyFor("Protected Veteran Status") === "veteran_status");
check(
  "disability → disability_status",
  complianceKeyFor("Voluntary Self-Identification of Disability") === "disability_status",
);
check("race → race_ethnicity", complianceKeyFor("Race / Ethnicity") === "race_ethnicity");
check("gender → gender", complianceKeyFor("Gender") === "gender");
check("unrelated question → null", complianceKeyFor("Why are you interested in this role?") === null);

// The EEO four are flagged distinctly from sponsorship/work-auth.
check("veteran is an EEO key", EEO_KEYS.has("veteran_status"));
check("sponsorship is NOT an EEO key", !EEO_KEYS.has("requires_sponsorship"));

// ── complianceValue: returns the saved value, "" when unset (never inferred) ──
const profile = {
  requires_sponsorship: "Yes",
  work_authorization: "Authorized to work in the US",
  veteran_status: "I am not a protected veteran",
  // disability/race/gender deliberately UNSET → must stay blank.
};
check("set value returned", complianceValue("requires_sponsorship", profile) === "Yes");
check("set EEO value returned", complianceValue("veteran_status", profile) === "I am not a protected veteran");
check("unset EEO → blank (disability)", complianceValue("disability_status", profile) === "");
check("unset EEO → blank (race)", complianceValue("race_ethnicity", profile) === "");
check("unset EEO → blank (gender)", complianceValue("gender", profile) === "");
check("null profile → blank", complianceValue("gender", null) === "");

// ── setSelectByText: matches option TEXT loosely; never selects a fallback ───
function selectStub(optionTexts) {
  let value = "";
  let changed = 0;
  return {
    options: optionTexts.map((t) => ({ textContent: t, value: t })),
    set value(v) {
      value = v;
    },
    get value() {
      return value;
    },
    get changes() {
      return changed;
    },
    dispatchEvent() {
      changed++;
      return true;
    },
  };
}

{
  // Saved "Yes" matches a longer option label loosely.
  const el = selectStub(["Please select", "Yes, I require sponsorship", "No"]);
  check("sponsorship 'Yes' matches a longer option", setSelectByText(el, "Yes") === true);
  check("sponsorship selected the right option", el.value === "Yes, I require sponsorship");
  check("change event fired", el.changes === 1);
}
{
  // Exact EEO option text.
  const el = selectStub([
    "Decline to self-identify",
    "I am not a protected veteran",
    "I identify as one or more of the classifications of a protected veteran",
  ]);
  check("veteran exact match", setSelectByText(el, "I am not a protected veteran") === true);
  check("veteran option selected", el.value === "I am not a protected veteran");
}
{
  // No matching option → leave untouched, return false (never pick a default).
  const el = selectStub(["Please select", "Male", "Female"]);
  check("no match → false", setSelectByText(el, "Non-binary") === false);
  check("no match → value untouched", el.value === "");
  check("no match → no change event", el.changes === 0);
}
{
  // Blank value → never touches the field.
  const el = selectStub(["A", "B"]);
  check("blank value → false", setSelectByText(el, "") === false);
  check("blank → value untouched", el.value === "");
}

// country is a recognised contact field (standard fill), distinct from compliance.
check("country recognised as a contact field", contactKeyFor("Country") === "country");

console.log(`compliance.test: ${passed} assertions passed`);
