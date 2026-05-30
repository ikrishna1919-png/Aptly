"""Deterministic JD keyword-coverage scoring.

This is the HONEST alternative to a made-up "ATS score": we extract the
concrete terms a job description screens for and compute the percentage that
actually appear in a candidate's text (profile or a generated resume). No LLM,
no invented number — just measured overlap, reported as "JD keyword coverage".

`score(jd_text, candidate_text)` returns the percentage + the matched/missing
term lists so the UI can show exactly which keywords landed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Curated technical vocabulary (languages, frameworks, cloud, data, practices).
# Multi-word entries are matched as phrases; single words as tokens. This is a
# pragmatic, transparent list — not exhaustive, but every hit is a real term
# the JD used and the resume covers.
_KNOWN_TERMS: tuple[str, ...] = (
    # languages
    "python",
    "java",
    "javascript",
    "typescript",
    "go",
    "golang",
    "rust",
    "c++",
    "c#",
    "ruby",
    "scala",
    "kotlin",
    "swift",
    "php",
    "r",
    "matlab",
    "sql",
    # web / frameworks
    "react",
    "next.js",
    "nextjs",
    "vue",
    "angular",
    "svelte",
    "node.js",
    "nodejs",
    "express",
    "django",
    "flask",
    "fastapi",
    "spring",
    "rails",
    "graphql",
    "rest",
    "grpc",
    "tailwind",
    # data / ml
    "pandas",
    "numpy",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "spark",
    "hadoop",
    "kafka",
    "airflow",
    "dbt",
    "snowflake",
    "databricks",
    "etl",
    "machine learning",
    "deep learning",
    "nlp",
    "llm",
    "data pipeline",
    "data engineering",
    # cloud / infra
    "aws",
    "azure",
    "gcp",
    "google cloud",
    "kubernetes",
    "docker",
    "terraform",
    "ansible",
    "jenkins",
    "ci/cd",
    "cicd",
    "github actions",
    "lambda",
    "s3",
    "ec2",
    "rds",
    "dynamodb",
    "serverless",
    "microservices",
    # databases
    "postgres",
    "postgresql",
    "mysql",
    "mongodb",
    "redis",
    "elasticsearch",
    "cassandra",
    # practices
    "agile",
    "scrum",
    "tdd",
    "unit testing",
    "code review",
    "distributed systems",
    "event-driven",
    "observability",
    "monitoring",
    "devops",
    "sre",
    # roles / domains
    "backend",
    "frontend",
    "full stack",
    "full-stack",
    "api",
    "platform",
    "security",
    "authentication",
    "oauth",
)

_STOPWORDS = {
    "and",
    "or",
    "the",
    "a",
    "an",
    "to",
    "of",
    "in",
    "on",
    "for",
    "with",
    "as",
    "is",
    "are",
    "be",
    "you",
    "your",
    "we",
    "our",
    "will",
    "have",
    "has",
    "this",
    "that",
    "at",
    "by",
    "from",
    "experience",
    "years",
    "team",
    "work",
    "working",
    "ability",
    "strong",
    "knowledge",
    "skills",
    "requirements",
    "responsibilities",
    "preferred",
    "plus",
    "etc",
    "including",
    "across",
    "using",
    "build",
    "building",
}

# A token may contain internal +/#/./- (c++, ci/cd, next.js) but never leads or
# trails with punctuation — so "plus." and "postgresql." tokenize cleanly.
_WORD = re.compile(r"[a-z0-9]+(?:[+#./\-][a-z0-9]+)*[+#]?")


@dataclass(frozen=True)
class Coverage:
    percent: int
    matched: list[str]
    missing: list[str]

    def to_dict(self) -> dict:
        return {"percent": self.percent, "matched": self.matched, "missing": self.missing}


def _normalize(text: str) -> str:
    return (text or "").lower()


def _contains_term(haystack: str, term: str) -> bool:
    """Whole-word/phrase containment. Multi-word terms match as a substring on
    word boundaries; single tokens match exactly so 'go' doesn't hit 'google'."""
    if " " in term or "/" in term or "-" in term and term not in haystack:
        # Phrase-ish: simple boundary-aware substring.
        pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])"
        return re.search(pattern, haystack) is not None
    pattern = r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9+#])"
    return re.search(pattern, haystack) is not None


def extract_jd_terms(jd_text: str, *, limit: int = 30) -> list[str]:
    """The terms a JD screens for: known technical terms it mentions, plus the
    most frequent salient non-stopword tokens as a fallback so non-technical
    JDs still produce a signal. Deduped, order-stable, capped."""
    jd = _normalize(jd_text)
    terms: list[str] = []
    seen: set[str] = set()

    for term in _KNOWN_TERMS:
        if _contains_term(jd, term) and term not in seen:
            terms.append(term)
            seen.add(term)

    if len(terms) < limit:
        # Frequency-rank remaining salient tokens (len>=3, not a stopword).
        freq: dict[str, int] = {}
        for tok in _WORD.findall(jd):
            if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
                continue
            freq[tok] = freq.get(tok, 0) + 1
        for tok, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
            if len(terms) >= limit:
                break
            if tok not in seen:
                terms.append(tok)
                seen.add(tok)

    return terms[:limit]


def score(jd_text: str, candidate_text: str, *, limit: int = 30) -> Coverage:
    """JD keyword coverage of `candidate_text`. Percent is matched/total of the
    extracted JD terms (0 when the JD yields no terms)."""
    terms = extract_jd_terms(jd_text, limit=limit)
    if not terms:
        return Coverage(percent=0, matched=[], missing=[])
    hay = _normalize(candidate_text)
    matched = [t for t in terms if _contains_term(hay, t)]
    missing = [t for t in terms if t not in matched]
    percent = round(100 * len(matched) / len(terms))
    return Coverage(percent=percent, matched=matched, missing=missing)


def candidate_text_from_profile(profile: dict) -> str:
    """Flatten a profile dict into searchable text for scoring."""
    parts: list[str] = []
    for key in ("summary", "headline", "name"):
        v = profile.get(key)
        if isinstance(v, str):
            parts.append(v)
    skills = profile.get("skills") or []
    for s in skills:
        if isinstance(s, str):
            parts.append(s)
        elif isinstance(s, dict):
            parts.extend(str(x) for x in (s.get("items") or []))
    for exp in profile.get("experience") or []:
        if isinstance(exp, dict):
            parts.append(str(exp.get("title", "")))
            parts.append(str(exp.get("company", "")))
            parts.extend(str(b) for b in (exp.get("bullets") or []))
    for proj in profile.get("projects") or []:
        if isinstance(proj, dict):
            parts.append(str(proj.get("name", "")))
            parts.append(str(proj.get("description", "")))
            parts.extend(str(b) for b in (proj.get("bullets") or []))
    return " ".join(parts)


def candidate_text_from_resume(resume: dict) -> str:
    """Flatten a generated resume (TailoredResume JSON) into searchable text."""
    parts: list[str] = [str(resume.get("summary", ""))]
    for g in resume.get("skills") or []:
        if isinstance(g, dict):
            parts.extend(str(x) for x in (g.get("items") or []))
    for e in resume.get("experience") or []:
        if isinstance(e, dict):
            parts.append(str(e.get("title", "")))
            parts.append(str(e.get("company", "")))
            parts.extend(str(b) for b in (e.get("bullets") or []))
    for p in resume.get("projects") or []:
        if isinstance(p, dict):
            parts.append(str(p.get("name", "")))
            parts.append(str(p.get("description", "")))
            parts.extend(str(b) for b in (p.get("bullets") or []))
    return " ".join(parts)
