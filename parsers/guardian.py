"""Headline extractor for theguardian.com."""

from __future__ import annotations

import re

from .base import CandidateCollector

DOMAINS = ("theguardian.com", "guardian.com")
NAME = "The Guardian"
LANGUAGE = "en"
DEFAULT_URL = "https://www.theguardian.com/international"

_ARTICLE_HREF = re.compile(
    r"/20\d{2}/[a-z]{3}/\d{2}/|/live/20\d{2}/|/ng-interactive/20\d{2}/",
    re.IGNORECASE,
)
_SKIP_HREF = re.compile(
    r"/info/|/help/|/email/|/preference/|/crosswords/|/games/|"
    r"/tone/advertisement-features|/sign-up|/newsletter",
    re.IGNORECASE,
)
_DCR_QUOTE = re.compile(
    r"\b(?:double|single)\s+quotation\s+mark\b",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    text = _DCR_QUOTE.sub("", text or "")
    return re.sub(r"\s+", " ", text).strip(" -–—|")


def _article_link_from(el):
    node = el
    for _ in range(8):
        if node is None:
            return None
        a = node.find("a", href=True)
        if a:
            href = a.get("href") or ""
            if _ARTICLE_HREF.search(href) and not _SKIP_HREF.search(href):
                return a
        node = node.parent
    return None


def extract(soup, base_url: str):
    c = CandidateCollector(base_url)

    for a in soup.select('a[data-link-name*="article"]'):
        href = a.get("href") or ""
        if not _ARTICLE_HREF.search(href) or _SKIP_HREF.search(href):
            continue
        text = _clean_text(a.get_text(" ", strip=True) or a.get("aria-label") or "")
        if ", " in text and len(text) > 100:
            text = text.split(", ", 1)[0].strip()
        if len(text) < 20:
            continue
        low = text.lower()
        if low.startswith(("sign up", "tell us:", "support the guardian")):
            continue
        c.add(text, href, score=9)

    for h in soup.select(".card-headline, h3.card-headline, h4.card-headline"):
        text = _clean_text(h.get_text(" ", strip=True))
        # Some cards concatenate several teasers; keep the first chunk.
        if ", " in text and len(text) > 90:
            text = text.split(", ", 1)[0].strip()
        if len(text) < 20:
            continue
        a = _article_link_from(h)
        if not a:
            continue
        c.add(text, a.get("href"), score=7)

    return c.results()
