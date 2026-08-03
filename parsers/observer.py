"""Headline extractor for observer.co.uk (The Observer)."""

from __future__ import annotations

import re

from .base import CandidateCollector

DOMAINS = ("observer.co.uk",)
NAME = "The Observer"
LANGUAGE = "en"
DEFAULT_URL = "https://observer.co.uk/"

_ARTICLE_HREF = re.compile(r"/article/", re.IGNORECASE)
_SKIP_HREF = re.compile(
    r"/culture-club/?$|/newsletter|/subscribe|/login|/account",
    re.IGNORECASE,
)


def extract(soup, base_url: str):
    c = CandidateCollector(base_url)

    for h in soup.find_all(["h1", "h2", "h3"]):
        a = h.find("a", href=True)
        if not a:
            a = h.find_parent("a", href=True)
        if not a:
            continue
        href = a.get("href") or ""
        if not _ARTICLE_HREF.search(href) or _SKIP_HREF.search(href):
            continue
        text = h.get_text(" ", strip=True)
        if len(text) < 18:
            continue
        c.add(text, href, score=9)

    return c.results()
