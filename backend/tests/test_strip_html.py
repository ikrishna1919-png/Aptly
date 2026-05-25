"""Unit tests for strip_html / looks_like_html.

Pinning the behavior these tests describe is important — the JD field
piped into the Anthropic prompt comes through this helper, and a regression
to "single-line wall of text" silently degrades model output quality.
"""

from __future__ import annotations

from app.sources._text import looks_like_html, strip_html


def test_empty_inputs():
    assert strip_html(None) == ""
    assert strip_html("") == ""
    # Whitespace-only is also empty after trimming.
    assert strip_html("   \n  \n  ") == ""


def test_html_entities_decoded():
    assert strip_html("Build &amp; ship") == "Build & ship"
    assert strip_html("Use &lt;Tag&gt; carefully") == "Use <Tag> carefully"
    # &nbsp; (U+00A0) is intentionally normalized to a plain space — it's
    # just noise in JD text and the LLM doesn't benefit from the distinction.
    assert strip_html("salary&nbsp;range") == "salary range"


def test_paragraphs_become_newlines():
    html = "<p>Para one.</p><p>Para two.</p>"
    assert strip_html(html) == "Para one.\n\nPara two."


def test_br_becomes_newline():
    html = "Line one<br/>Line two<br>Line three"
    assert strip_html(html) == "Line one\nLine two\nLine three"


def test_lists_become_bulleted_lines():
    html = "<p>Skills:</p><ul><li>Python</li><li>Kafka</li><li>AWS</li></ul>"
    cleaned = strip_html(html)
    assert "Skills:" in cleaned
    assert "- Python" in cleaned
    assert "- Kafka" in cleaned
    assert "- AWS" in cleaned


def test_nested_tags_and_attributes():
    html = (
        '<div class="jd"><p><strong>About the role.</strong> '
        "Build <em>distributed</em> systems.</p></div>"
    )
    cleaned = strip_html(html)
    assert "About the role." in cleaned
    assert "Build distributed systems." in cleaned
    assert "<" not in cleaned and ">" not in cleaned


def test_collapses_horizontal_whitespace_but_keeps_paragraphs():
    html = "<p>A    B</p>\n\n\n<p>C\tD</p>"
    cleaned = strip_html(html)
    # Horizontal runs collapse, paragraphs survive with a single blank line.
    assert cleaned == "A B\n\nC D"


def test_realistic_greenhouse_html():
    raw = (
        "<div><h2>About Us</h2>"
        "<p>We&apos;re building fast and reliable systems.</p>"
        "<h3>What you&apos;ll do</h3>"
        "<ul>"
        "<li>Design and build event-driven services in Python and Kafka.</li>"
        "<li>Deploy on AWS with Kubernetes.</li>"
        "</ul>"
        "<h3>Requirements</h3>"
        "<ul><li>5+ years backend experience</li></ul></div>"
    )
    cleaned = strip_html(raw)
    # No HTML left.
    assert "<" not in cleaned and ">" not in cleaned
    assert "&apos;" not in cleaned and "&amp;" not in cleaned
    # Structure preserved.
    assert "About Us" in cleaned
    assert "- Design and build event-driven services in Python and Kafka." in cleaned
    assert "- Deploy on AWS with Kubernetes." in cleaned
    # Sections are separated by blank lines.
    assert "About Us\n\n" in cleaned
    # Never collapses everything to a single line.
    assert cleaned.count("\n") >= 4


def test_looks_like_html_positive_signals():
    assert looks_like_html("<p>hello</p>") is True
    assert looks_like_html("plain &amp; entity") is True
    assert looks_like_html("no markup but &#8212; dash") is True


def test_looks_like_html_negative_signals():
    assert looks_like_html(None) is False
    assert looks_like_html("") is False
    assert looks_like_html("Just plain text, no markup.") is False
    assert looks_like_html("Hello & goodbye") is False  # bare & isn't an entity
