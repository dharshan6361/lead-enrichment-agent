"""Strip raw HTML down to clean, LLM-friendly text.

Goal: never hand raw HTML trees to the LLM. We drop script/style/svg/nav/footer
boilerplate, collapse whitespace, and cap length per page so token usage stays
predictable across many domains.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

# Tags that carry no useful natural-language content for this task.
STRIP_TAGS = ["script", "style", "svg", "noscript", "iframe", "path", "nav", "footer", "form"]

# Cap on characters kept per page after cleaning, to bound LLM token spend.
MAX_CHARS_PER_PAGE = 6_000


def clean_html_to_text(html: str) -> str:
    """Convert raw HTML into compact plain text suitable for LLM extraction."""
    soup = BeautifulSoup(html, "html.parser")

    for tag_name in STRIP_TAGS:
        for tag in soup.find_all(tag_name):
            tag.decompose()

    # Preserve mailto/https hrefs inline next to their link text - this is how we
    # recover emails and LinkedIn URLs without needing raw HTML.
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("mailto:") or "linkedin.com" in href:
            a.append(f" ({href})")

    text = soup.get_text(separator="\n")
    # Collapse excess blank lines / whitespace introduced by stripping tags.
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text[:MAX_CHARS_PER_PAGE]


def extract_raw_emails(html: str) -> list[str]:
    """Regex fallback for emails, in case the LLM misses something obvious.

    Decodes literal \\uXXXX escape sequences first (these show up when the
    regex scans raw <script> JSON-LD blobs before tag-stripping) - otherwise
    a stray "\\u003e" (">") glued to an email in the source can get swallowed
    into the match, e.g. "u003ehelp@postman.com" instead of "help@postman.com".
    """
    unescaped = re.sub(
        r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), html
    )
    return sorted(set(re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", unescaped)))
