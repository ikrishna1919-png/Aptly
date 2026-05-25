from app.services.skills import extract_skills


def test_finds_languages_and_frameworks():
    text = "We need someone strong in Python and React, with FastAPI experience."
    skills = extract_skills(text)
    assert "Python" in skills
    assert "React" in skills
    assert "FastAPI" in skills


def test_case_insensitive_and_deduped():
    text = "PYTHON python Python; we love Postgres and PostgreSQL."
    skills = extract_skills(text)
    assert skills.count("Python") == 1
    assert skills.count("PostgreSQL") == 1


def test_no_match_returns_empty():
    assert extract_skills("We're hiring a generalist with strong communication.") == []


def test_empty_or_none_input():
    assert extract_skills(None) == []
    assert extract_skills("") == []


def test_symbol_languages_match():
    skills = extract_skills("Looking for C++ and C# devs, plus some .NET work.")
    assert "C++" in skills
    assert "C#" in skills


def test_word_boundary_avoids_false_positives():
    # "go" inside "going" should NOT match; "Go" with word boundaries should.
    assert "Go" not in extract_skills("We're going to the office")
    assert "Go" in extract_skills("Strong Go and Rust experience.")
