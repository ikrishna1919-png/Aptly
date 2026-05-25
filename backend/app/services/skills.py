"""Cheap keyword-based skill extraction.

Phase 1 keeps this deliberately simple: a curated list, case-insensitive
word-boundary match against the cleaned JD text. Phase 4 will use Claude
for proper extraction. Until then, this is enough for filterable chips.
"""

from __future__ import annotations

import re

# (canonical form, regex-escaped match pattern) — patterns are matched
# case-insensitive with word boundaries (or specific delimiters for symbols).
_SKILL_PATTERNS: list[tuple[str, str]] = [
    # Languages
    ("Python", r"python"),
    ("JavaScript", r"javascript|\bjs\b"),
    ("TypeScript", r"typescript|\bts\b"),
    ("Go", r"\bgolang\b|\bgo\b"),
    ("Rust", r"\brust\b"),
    ("Java", r"\bjava\b"),
    ("Kotlin", r"\bkotlin\b"),
    ("Swift", r"\bswift\b"),
    ("Ruby", r"\bruby\b"),
    ("Scala", r"\bscala\b"),
    ("C++", r"c\+\+"),
    ("C#", r"c#|\.net"),
    ("PHP", r"\bphp\b"),
    ("SQL", r"\bsql\b"),
    # Frontend
    ("React", r"\breact\b"),
    ("Next.js", r"next\.?js"),
    ("Vue", r"\bvue\b"),
    ("Angular", r"\bangular\b"),
    ("Svelte", r"\bsvelte\b"),
    ("Tailwind", r"\btailwind\b"),
    # Backend / frameworks
    ("FastAPI", r"\bfastapi\b"),
    ("Django", r"\bdjango\b"),
    ("Flask", r"\bflask\b"),
    ("Rails", r"\brails\b"),
    ("Spring", r"\bspring\b"),
    ("Node.js", r"node\.?js"),
    ("Express", r"\bexpress\b"),
    ("GraphQL", r"\bgraphql\b"),
    ("REST", r"\brest(ful)?\b"),
    ("gRPC", r"\bgrpc\b"),
    # Data / ML
    ("PostgreSQL", r"postgres(?:ql)?"),
    ("MySQL", r"\bmysql\b"),
    ("MongoDB", r"\bmongo(db)?\b"),
    ("Redis", r"\bredis\b"),
    ("Kafka", r"\bkafka\b"),
    ("Spark", r"\bspark\b"),
    ("Airflow", r"\bairflow\b"),
    ("dbt", r"\bdbt\b"),
    ("Snowflake", r"\bsnowflake\b"),
    ("PyTorch", r"\bpytorch\b"),
    ("TensorFlow", r"\btensorflow\b"),
    ("LLM", r"\bllms?\b|large language model"),
    ("Claude", r"\bclaude\b"),
    ("GPT", r"\bgpt-?\d?\b|openai"),
    # Cloud / infra
    ("AWS", r"\baws\b|amazon web services"),
    ("GCP", r"\bgcp\b|google cloud"),
    ("Azure", r"\bazure\b"),
    ("Kubernetes", r"\bk(?:ubernetes|8s)\b"),
    ("Docker", r"\bdocker\b"),
    ("Terraform", r"\bterraform\b"),
    ("CI/CD", r"\bci/?cd\b"),
]

_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pat, re.IGNORECASE)) for name, pat in _SKILL_PATTERNS
]


def extract_skills(text: str | None) -> list[str]:
    """Return the de-duplicated, order-preserving list of skills mentioned."""
    if not text:
        return []
    found: list[str] = []
    seen: set[str] = set()
    for name, pattern in _COMPILED:
        if name in seen:
            continue
        if pattern.search(text):
            found.append(name)
            seen.add(name)
    return found
