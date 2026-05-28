"""Tests for the comprehensive resume-parsing overhaul.

Covers the additions made when the parser grew support for the
"missing sections" round of work — Summary / Languages / Volunteer /
Publications / Affiliations / additional-sections + website + GPA +
field_of_study + headline inference.

These tests sit alongside the existing `test_profile.py` suite (which
covers contact-field extraction, hybrid LLM/regex merge, and the
background-worker resilience contract). The split is deliberate: the
new file exercises the OVERHAUL semantics — schema shape, converter
behaviour, headline inference rule — without poking the FastAPI
routing or the worker thread.
"""

from __future__ import annotations

from app.services import profile_parser as parser_module
from app.services.profile_parser import (
    Profile,
    ProfileExperience,
    _apply_headline_inference,
    _estimate_total_experience_years,
    _LLMAdditionalSection,
    _LLMAffiliation,
    _LLMLanguage,
    _LLMPublication,
    _LLMVolunteer,
    _to_profile_additional,
    _to_profile_affiliation,
    _to_profile_language,
    _to_profile_publication,
    _to_profile_volunteer,
)

# ─── Schema shape ───────────────────────────────────────────────────────────


def test_llm_schema_includes_new_sections():
    """Every section the overhaul added must surface as a top-level
    property in the schema sent to Anthropic — otherwise the model
    has no slot to return them and the parser silently drops them."""
    props = parser_module._LLM_SCHEMA["properties"]
    for key in (
        "headline",
        "summary",
        "website_url",
        "languages",
        "volunteer",
        "publications",
        "affiliations",
        "additional_sections",
    ):
        assert key in props, f"missing top-level property: {key}"

    # `_LLMEducation` carries field_of_study + gpa as their own slots.
    edu_props = parser_module._LLM_SCHEMA["$defs"]["_LLMEducation"]["properties"]
    assert "field_of_study" in edu_props
    assert "gpa" in edu_props


def test_llm_schema_is_anthropic_clean():
    """Anthropic structured output rejects schemas with `default`,
    `title`-the-annotation, `minimum`/`maximum`, or any object node
    without `additionalProperties: false`. `prepare_schema` strips
    those and adds the property; if a future refactor breaks the
    invariant, the API will 400 in prod. Catch it locally."""
    import json

    schema = parser_module._LLM_SCHEMA
    flat = json.dumps(schema)
    for forbidden in ('"default":', '"minimum":', '"maximum":', '"exclusiveMinimum":'):
        assert forbidden not in flat, f"schema contains forbidden keyword: {forbidden}"

    # Every object node has additionalProperties: false.
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert (
                    node.get("additionalProperties") is False
                ), "object node missing additionalProperties: false"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)


# ─── Converter behaviour ────────────────────────────────────────────────────


def test_language_converter_drops_blank_entries():
    assert _to_profile_language(_LLMLanguage(name=None, proficiency=None)) is None
    assert _to_profile_language(_LLMLanguage(name="  ", proficiency="Fluent")) is None

    out = _to_profile_language(_LLMLanguage(name="Spanish", proficiency="Native"))
    assert out is not None
    assert out.name == "Spanish"
    assert out.proficiency == "Native"

    # Empty proficiency normalises to None (not the empty string),
    # which keeps the saved JSON tidy.
    out = _to_profile_language(_LLMLanguage(name="Spanish", proficiency=""))
    assert out is not None
    assert out.proficiency is None


def test_volunteer_converter_requires_at_least_one_anchor():
    # All-empty noise row → dropped.
    assert _to_profile_volunteer(_LLMVolunteer()) is None

    # Role only, no org — surface it under `organization` so the
    # entry isn't silently dropped; better something editable than
    # nothing.
    out = _to_profile_volunteer(_LLMVolunteer(role="Team Lead"))
    assert out is not None
    assert out.organization == "Team Lead"

    out = _to_profile_volunteer(
        _LLMVolunteer(
            organization="Habitat for Humanity",
            role="Site Coordinator",
            description="Led weekend builds.",
            location="Detroit, MI",
            start_date="2021",
            end_date="2023",
            bullets=["Recruited 30 volunteers", "Raised $5k for materials"],
        )
    )
    assert out is not None
    assert out.organization == "Habitat for Humanity"
    assert out.role == "Site Coordinator"
    assert out.location == "Detroit, MI"
    assert len(out.bullets) == 2


def test_publication_converter_requires_title():
    assert _to_profile_publication(_LLMPublication()) is None
    assert _to_profile_publication(_LLMPublication(title="  ")) is None

    out = _to_profile_publication(
        _LLMPublication(
            title="On Adversarial Robustness in Vision Models",
            venue="NeurIPS 2024",
            date="Dec 2024",
            authors="Smith, J.; Doe, A.; et al.",
        )
    )
    assert out is not None
    assert out.venue == "NeurIPS 2024"
    assert out.authors == "Smith, J.; Doe, A.; et al."


def test_affiliation_converter_requires_name():
    assert _to_profile_affiliation(_LLMAffiliation()) is None
    out = _to_profile_affiliation(_LLMAffiliation(name="IEEE", role="Member"))
    assert out is not None
    assert out.name == "IEEE"
    assert out.role == "Member"


def test_additional_section_converter_keeps_label_or_content():
    # Both blank — drop.
    assert _to_profile_additional(_LLMAdditionalSection()) is None

    # Content but no label — surface under a generic label so the
    # user can edit, rather than losing the body silently.
    out = _to_profile_additional(_LLMAdditionalSection(content="Loves trail running."))
    assert out is not None
    assert out.label == "Additional"
    assert out.content == "Loves trail running."

    out = _to_profile_additional(
        _LLMAdditionalSection(label="Hobbies", content="Trail running, cooking.")
    )
    assert out is not None
    assert out.label == "Hobbies"


# ─── Headline inference ─────────────────────────────────────────────────────


def test_headline_inference_skipped_when_resume_has_one():
    """If the resume surfaced a headline verbatim, leave it alone —
    don't overwrite, don't flip the inferred flag."""
    p = Profile(
        name="Ada",
        headline="ML Researcher",
        experience=[
            ProfileExperience(
                company="Acme",
                title="Senior Engineer",
                start="2020",
                end="Present",
            )
        ],
    )
    out = _apply_headline_inference(p)
    assert out.headline == "ML Researcher"
    assert out.headline_inferred is False


def test_headline_inference_uses_most_recent_role_and_years():
    """No headline + multi-year history → '<title> · N years experience',
    flagged as inferred."""
    p = Profile(
        name="Ada",
        experience=[
            ProfileExperience(
                company="Acme",
                title="Senior Data Engineer",
                start="2019",
                end="Present",
            ),
            ProfileExperience(
                company="OldCo",
                title="Data Engineer",
                start="2017",
                end="2019",
            ),
        ],
    )
    out = _apply_headline_inference(p)
    assert out.headline_inferred is True
    assert "Senior Data Engineer" in out.headline
    # Span from 2017 to the current year. Don't pin the exact number
    # — the date heuristic uses datetime.now() which makes the test
    # date-dependent. Just check the shape.
    assert "year" in out.headline


def test_headline_inference_skips_when_no_usable_experience():
    """No experience, no inference — better to leave headline null
    than fabricate one out of nothing."""
    p = Profile(name="Ada")
    out = _apply_headline_inference(p)
    assert out.headline is None
    assert out.headline_inferred is False


def test_headline_inference_with_title_only_no_dates():
    """Most-recent role present but with unparseable dates → headline
    is just the title (no fabricated year count)."""
    p = Profile(
        name="Ada",
        experience=[
            ProfileExperience(
                company="Acme",
                title="Software Engineer",
                start="",
                end="",
            )
        ],
    )
    out = _apply_headline_inference(p)
    assert out.headline == "Software Engineer"
    assert out.headline_inferred is True


def test_estimate_total_experience_years_handles_present():
    """The 'Present' end-date sentinel should resolve to the current
    year so a 2020 → Present role doesn't return 0."""
    from datetime import UTC, datetime

    current = datetime.now(UTC).year
    out = _estimate_total_experience_years(
        [ProfileExperience(company="Acme", title="X", start="2020", end="Present")]
    )
    assert out == current - 2020


# ─── End-to-end: PDF path surfaces new sections ─────────────────────────────


def test_llm_to_profile_carries_all_new_sections():
    """`_llm_to_profile` is the PDF path's converter. Verify every new
    section flows through to the final Profile (no silent drops)."""
    llm = parser_module._LLMStructuralExtract(
        name="Alex Rivera",
        headline="Staff Engineer",
        summary="Senior backend engineer with 8 years of experience.",
        email="alex@example.com",
        phone="+1 415 555 0100",
        location="San Francisco, CA",
        linkedin_url="linkedin.com/in/alex",
        github_url="github.com/alex",
        website_url="https://alex.dev",
        experience=[],
        education=[
            parser_module._LLMEducation(
                school="MIT",
                degree="B.S.",
                field_of_study="Computer Science",
                gpa="3.9/4.0",
                start_date="2014",
                end_date="2018",
            )
        ],
        skills=["Python"],
        languages=[parser_module._LLMLanguage(name="Spanish", proficiency="Fluent")],
        volunteer=[
            parser_module._LLMVolunteer(
                organization="Habitat for Humanity",
                role="Volunteer",
            )
        ],
        publications=[parser_module._LLMPublication(title="On X", venue="NeurIPS 2024")],
        affiliations=[parser_module._LLMAffiliation(name="IEEE", role="Member")],
        additional_sections=[
            parser_module._LLMAdditionalSection(label="Hobbies", content="Trail running.")
        ],
    )
    profile = parser_module._llm_to_profile(llm)

    assert profile.headline == "Staff Engineer"
    assert profile.summary.startswith("Senior backend engineer")
    assert profile.links.website == "https://alex.dev"
    assert profile.education[0].field_of_study == "Computer Science"
    assert profile.education[0].gpa == "3.9/4.0"
    assert profile.languages[0].name == "Spanish"
    assert profile.volunteer[0].organization == "Habitat for Humanity"
    assert profile.publications[0].title == "On X"
    assert profile.affiliations[0].name == "IEEE"
    assert profile.additional_sections[0].label == "Hobbies"


def test_merge_text_path_carries_all_new_sections():
    """Same coverage as above but for `_merge` — the TEXT path the
    paste / DOCX flows take. Both converters must surface the new
    sections so PDF vs paste don't drift."""
    regex = Profile(name="")
    llm = parser_module._LLMStructuralExtract(
        name="Alex",
        website_url="https://alex.dev",
        languages=[parser_module._LLMLanguage(name="French")],
        volunteer=[parser_module._LLMVolunteer(organization="Red Cross")],
        publications=[parser_module._LLMPublication(title="Paper Y")],
        affiliations=[parser_module._LLMAffiliation(name="ACM")],
        additional_sections=[
            parser_module._LLMAdditionalSection(label="Patents", content="US12345")
        ],
    )
    merged = parser_module._merge(regex, llm)
    assert merged.links.website == "https://alex.dev"
    assert merged.languages[0].name == "French"
    assert merged.volunteer[0].organization == "Red Cross"
    assert merged.publications[0].title == "Paper Y"
    assert merged.affiliations[0].name == "ACM"
    assert merged.additional_sections[0].label == "Patents"
