"""Pin the company-token list shape + that the two requested tokens
(vulcanelements, sigmacomputing) are present so a careless edit can't
silently drop them on next deploy."""

from __future__ import annotations

from app.sources.companies import COMPANIES, GREENHOUSE_TOKENS, LEVER_TOKENS


def test_greenhouse_tokens_includes_new_companies():
    # The two requested in this round.
    assert "vulcanelements" in GREENHOUSE_TOKENS
    assert "sigmacomputing" in GREENHOUSE_TOKENS


def test_companies_is_composed_from_the_per_source_lists():
    """The (source, token) tuple list ingest consumes is derived from the
    flat per-source lists — no entries get added directly. Mismatch
    means someone hand-edited COMPANIES instead of the lists above."""
    expected = [("greenhouse", t) for t in GREENHOUSE_TOKENS] + [("lever", t) for t in LEVER_TOKENS]
    assert COMPANIES == expected


def test_no_duplicate_tokens_within_a_source():
    """A typo'd duplicate would waste an HTTP probe per ingest and
    confuse the validation report. Keep each list deduplicated."""
    assert len(GREENHOUSE_TOKENS) == len(set(GREENHOUSE_TOKENS))
    assert len(LEVER_TOKENS) == len(set(LEVER_TOKENS))


def test_tokens_are_non_empty_strings():
    for t in GREENHOUSE_TOKENS + LEVER_TOKENS:
        assert isinstance(t, str)
        assert t.strip() == t, f"surrounding whitespace in {t!r}"
        assert t, "empty token"
