"""Regression tests for the four structural rules the parser MUST
get right on real resumes.

These were the failure modes observed on `Krishna_Chikkam_RESUME_AWS.docx`
(the golden reference) and across several other resumes the user
tested. Each test exercises ONE rule via a mocked LLM payload + the
real converter pipeline so the prompt change can never silently
regress on schema or converter behaviour. (The LLM behaviour itself
is exercised end-to-end against the live model — see the golden
JSON in `tests/fixtures/golden_parse_reference.json` for the
expected shape on a real resume.)

The rules:

* Pattern A — multiple roles under one company. A single company
  header followed by N titles produces N experience entries, all
  sharing the same company. Bullets stay with their parent role.
* Pattern B — tab-separated layout. `Company\\tLocation` and
  `Title\\tDates` lines split correctly; no swaps.
* Pattern C — categorised skills. `{category, items}` groups
  round-trip end-to-end; categories are NOT flattened or shredded.
* Pattern D — content over heading. A "Pat on the Back" award
  filed under a "Certifications" header is still classified as
  an achievement (and an "AWS Certified …" credential filed
  under "Honours" is still a certification).
"""

from __future__ import annotations

import json
from pathlib import Path

from app.services import profile_parser as parser_module
from app.services.profile_parser import (
    Profile,
    ProfileSkillGroup,
    _llm_to_profile,
)


def _build_llm_extract(**overrides) -> parser_module._LLMStructuralExtract:
    """Convenience constructor — only the fields a test sets need to
    be supplied; the rest default to the empty / null values the
    schema allows. Keeps the test cases compact."""
    defaults: dict = {
        "name": None,
        "headline": None,
        "summary": None,
        "email": None,
        "phone": None,
        "location": None,
        "linkedin_url": None,
        "github_url": None,
        "website_url": None,
        "experience": [],
        "education": [],
        "skills": [],
        "projects": [],
        "achievements": [],
        "certifications": [],
        "languages": [],
        "volunteer": [],
        "publications": [],
        "affiliations": [],
        "additional_sections": [],
        "section_order": [],
    }
    defaults.update(overrides)
    return parser_module._LLMStructuralExtract(**defaults)


# ─── Pattern A: multiple roles under one company ────────────────────────────


def test_multiple_roles_under_one_company_produce_separate_entries():
    """SMBC has TWO roles, Soulpage has TWO roles, Capgemini has ONE
    → 5 total experience entries, each carrying the correct company
    name. Bullets stay with their parent role."""
    llm = _build_llm_extract(
        experience=[
            parser_module._LLMExperience(
                company="SMBC Manu Bank",
                title="Senior Data Engineer",
                location="Scottsdale, Arizona",
                start_date="Aug 2023",
                end_date="Present",
                description_bullets=["Designed a Medallion Architecture data lake."],
            ),
            parser_module._LLMExperience(
                company="SMBC Manu Bank",
                title="Data Engineer Intern",
                location="Scottsdale, Arizona",
                start_date="Jan 2023",
                end_date="May 2023",
                description_bullets=["Built an AWS Glue pipeline."],
            ),
            parser_module._LLMExperience(
                company="Capgemini",
                title="AWS Data Engineer",
                location="Pune, India",
                start_date="Feb 2019",
                end_date="Nov 2021",
                description_bullets=["Migrated on-prem to AWS."],
            ),
            parser_module._LLMExperience(
                company="Soulpage IT Solutions",
                title="Data Engineer",
                location="Hyderabad, India",
                start_date="Jul 2018",
                end_date="Jan 2019",
                description_bullets=["Owned the ETL pipeline."],
            ),
            parser_module._LLMExperience(
                company="Soulpage IT Solutions",
                title="Data Engineer Intern",
                location="Hyderabad, India",
                start_date="Jan 2018",
                end_date="May 2018",
                description_bullets=["Shadowed the senior engineers."],
            ),
        ],
    )
    profile = _llm_to_profile(llm)

    # FIVE entries, not three. This is the most observable regression
    # signal: a merge would collapse SMBC+SMBC and Soulpage+Soulpage
    # into one entry each.
    assert len(profile.experience) == 5

    # Each entry carries the SAME company across the multi-role rows
    # (no "company missing on the second role" failure).
    companies = [e.company for e in profile.experience]
    assert companies == [
        "SMBC Manu Bank",
        "SMBC Manu Bank",
        "Capgemini",
        "Soulpage IT Solutions",
        "Soulpage IT Solutions",
    ]

    # Titles distinct per role (no title-bleed between roles).
    titles = [e.title for e in profile.experience]
    assert titles == [
        "Senior Data Engineer",
        "Data Engineer Intern",
        "AWS Data Engineer",
        "Data Engineer",
        "Data Engineer Intern",
    ]

    # Bullets stay with the parent role — the first SMBC bullet is on
    # the Senior entry, the second SMBC bullet is on the Intern entry.
    assert profile.experience[0].bullets == ["Designed a Medallion Architecture data lake."]
    assert profile.experience[1].bullets == ["Built an AWS Glue pipeline."]


# ─── Pattern B: tab-separated layout ────────────────────────────────────────


def test_tab_separated_layout_does_not_swap_company_location_or_title_dates():
    """The DOCX extractor preserves tab characters between
    `Company\\tLocation` and `Title\\tDates` shapes. Confirm the
    downstream parser respects the split (company on the left,
    location on the right; title on the left, dates on the right)
    — never swaps them."""
    llm = _build_llm_extract(
        experience=[
            parser_module._LLMExperience(
                company="Acme Corp",
                title="Senior Software Engineer",
                location="Brooklyn, NY",
                start_date="2021-01",
                end_date="Present",
                description_bullets=["Did the thing."],
            ),
        ],
    )
    profile = _llm_to_profile(llm)
    exp = profile.experience[0]
    # The four-way split: company ≠ location, title ≠ dates.
    assert exp.company == "Acme Corp"
    assert exp.location == "Brooklyn, NY"
    assert exp.title == "Senior Software Engineer"
    assert exp.start == "2021-01"
    assert exp.end == "Present"
    # And no date-shaped string accidentally bled into title.
    assert "2021" not in exp.title
    assert "Present" not in exp.title


def test_docx_paragraph_text_preserves_tabs():
    """The DOCX extractor's per-paragraph helper materialises
    `<w:tab/>` elements as `\\t`. Without this, the LLM sees
    'Senior Data EngineerAug 2023 – Present' as a single token and
    can't split title from dates. Test against a synthetic
    DOCX-style XML."""
    from unittest.mock import MagicMock

    from app.services.resume_extractor import _paragraph_text_with_tabs

    # Build a fake `Paragraph` shape: `.runs` is a list of `Run` mocks
    # whose `._r` is an iterable of fake XML children. Each child has
    # a `.tag` (Clark-notation) and `.text`.
    def child(tag: str, text: str | None = None):
        c = MagicMock()
        c.tag = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}" + tag
        c.text = text
        return c

    def run(*children):
        r = MagicMock()
        r._r = list(children)
        return r

    para = MagicMock()
    para.runs = [
        run(child("t", "Senior Software Engineer"), child("tab"), child("t", "Aug 2023 – Present")),
    ]
    out = _paragraph_text_with_tabs(para)
    assert out == "Senior Software Engineer\tAug 2023 – Present"


# ─── Pattern C: categorised skills preserved as groups ──────────────────────


def test_categorised_skills_round_trip_as_groups():
    """Skill groups returned by the LLM survive to the Profile as
    `[{category, items}]`. Earlier code flattened them, which broke
    the user's labelled view in the UI."""
    llm = _build_llm_extract(
        skills=[
            parser_module._LLMSkillGroup(category="Cloud Platforms", items=["AWS", "Azure", "GCP"]),
            parser_module._LLMSkillGroup(
                category="ETL & Data Engineering", items=["Spark", "Kafka", "Airflow"]
            ),
        ],
    )
    profile = _llm_to_profile(llm)
    assert isinstance(profile.skills[0], ProfileSkillGroup)
    assert profile.skills[0].category == "Cloud Platforms"
    assert profile.skills[0].items == ["AWS", "Azure", "GCP"]
    assert profile.skills[1].category == "ETL & Data Engineering"
    # And `flat_skills()` still produces the legacy flat shape for
    # any caller that needs it (tailor service, candidate fingerprint).
    assert profile.flat_skills() == [
        "AWS",
        "Azure",
        "GCP",
        "Spark",
        "Kafka",
        "Airflow",
    ]


def test_flat_skills_still_supported_when_resume_is_ungrouped():
    """Resumes without category labels return a flat list of strings —
    same shape legacy code paths expected. Don't force a fake category
    onto a flat list."""
    llm = _build_llm_extract(skills=["Python", "Kafka", "Airflow"])
    profile = _llm_to_profile(llm)
    assert profile.skills == ["Python", "Kafka", "Airflow"]
    assert profile.flat_skills() == ["Python", "Kafka", "Airflow"]


# ─── Pattern D: content over heading ────────────────────────────────────────


def test_pat_on_the_back_award_is_an_achievement_not_a_certification():
    """The user's resume files a 'Pat on the Back' award under the
    'Certifications' heading. The parser MUST sort it as an
    achievement based on its content (named recognition, no issuer
    org with a credential program) — not based on where it was
    filed."""
    # When the LLM correctly applies the rule, the award lands on the
    # `achievements` side and the credentials land on `certifications`.
    # This converter test pins the contract: once the model has done
    # the classification, the Profile mirrors it faithfully.
    llm = _build_llm_extract(
        achievements=[
            parser_module._LLMAchievement(
                title='"Pat on the Back" Award',
                description="Issued by VP at Capgemini Tech Services India Ltd",
            )
        ],
        certifications=[
            parser_module._LLMCertification(
                name="AWS Certified Data Analytics – Specialty", issuer="AWS"
            ),
            parser_module._LLMCertification(
                name="Microsoft Certified: Azure Data Engineer Associate (DP-203)",
                issuer="Microsoft",
            ),
        ],
    )
    profile = _llm_to_profile(llm)
    assert len(profile.achievements) == 1
    assert "Pat on the Back" in profile.achievements[0].title
    assert len(profile.certifications) == 2
    assert any("AWS" in c.name for c in profile.certifications)
    assert any("Azure" in c.name for c in profile.certifications)


def test_prompt_documents_all_four_layout_patterns():
    """The four structural rules MUST be present in the system prompt
    — otherwise the LLM has no guidance to follow. Verifying their
    presence here is the only test we can write without a live model
    call; it stops a future refactor from silently removing them."""
    prompt = parser_module._SYSTEM_PROMPT
    assert "PATTERN A" in prompt and "MULTIPLE ROLES UNDER ONE COMPANY" in prompt
    assert "PATTERN B" in prompt and "TAB-SEPARATED LINES" in prompt
    assert "PATTERN C" in prompt and "CATEGORISED SKILLS" in prompt
    assert "PATTERN D" in prompt and "CONTENT OVER HEADING" in prompt
    # Explicit example resume the user calls out — pin so the prompt
    # keeps the worked example for company-vs-title pairing.
    assert "SMBC" in prompt


# ─── Golden reference: structural conformance ───────────────────────────────


def _load_golden() -> dict:
    fixture = Path(__file__).parent / "fixtures" / "golden_parse_reference.json"
    return json.loads(fixture.read_text())


def test_golden_reference_validates_as_profile():
    """Lock the golden JSON against the Profile schema. If a future
    schema change breaks the round-trip — adds a required field, drops
    a key the golden uses, etc. — this test fails loudly with a clear
    Pydantic error so the breakage is caught before the regression
    reaches real users."""
    data = _load_golden()
    data.pop("_comment", None)  # the JSON carries a leading description key
    profile = Profile.model_validate(data)

    # Spot-check the pieces that prove the structural rules round-trip:
    #   * Five experience entries (the multi-role contract).
    #   * Same company on the two SMBC rows and the two Soulpage rows.
    #   * Categorised skills preserved as groups.
    #   * The "Pat on the Back" award on the achievements side, four
    #     credentials on the certifications side.
    assert len(profile.experience) == 5
    companies = [e.company for e in profile.experience]
    assert companies.count("SMBC Manu Bank") == 2
    assert companies.count("Soulpage IT Solutions") == 2
    assert companies.count("Capgemini") == 1

    # Skills are grouped — every entry has a category label.
    assert all(isinstance(s, ProfileSkillGroup) for s in profile.skills)
    cloud = next(s for s in profile.skills if s.category == "Cloud Platforms")
    assert any("AWS" in item for item in cloud.items)
    assert any("Azure" in item for item in cloud.items)

    # Achievement vs. certification: the award is on the right side
    # despite being filed under "Certifications" on the resume.
    assert len(profile.achievements) == 1
    assert "Pat on the Back" in profile.achievements[0].title
    assert len(profile.certifications) == 4

    # Education carries the new `coursework` + `gpa` + start/end pair.
    masters = profile.education[0]
    assert masters.gpa == "3.9"
    assert masters.start == "2022-01"
    assert masters.end == "2023-05"
    assert "Big Data Analytics" in masters.coursework

    # Project carries a `bullets` list (alongside the legacy
    # `description`/`technologies` fields).
    assert len(profile.projects) == 1
    assert len(profile.projects[0].bullets) == 2
    assert "Kite API (Zerodha)" in profile.projects[0].technologies
