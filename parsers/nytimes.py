"""Headline extractor for nytimes.com."""

from __future__ import annotations

import re

from .base import CandidateCollector

DOMAINS = ("nytimes.com",)
NAME = "The New York Times"
LANGUAGE = "en"
DEFAULT_URL = "https://www.nytimes.com/"

_ARTICLE_HREF = re.compile(
    r"(?:/20\d{2}/\d{2}/\d{2}/|/live/20\d{2}/|/interactive/20\d{2}/)",
    re.IGNORECASE,
)
_SKIP_HREF = re.compile(
    r"/athletic/|/wirecutter/|/cooking/|/crosswords/|/games/|/subscriptions?/|"
    r"/section/|/by/|/column/|/newsletters?/|/podcasts?/the-headlines",
    re.IGNORECASE,
)
_CREDIT = re.compile(
    r"(?:for The New York Times|/The New York Times$|^The Headlines Audio)",
    re.IGNORECASE,
)


def extract(soup, base_url: str):
    c = CandidateCollector(base_url)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _ARTICLE_HREF.search(href):
            continue
        if _SKIP_HREF.search(href):
            continue

        heading = a.find(["h1", "h2", "h3", "h4"])
        if heading:
            text = heading.get_text(" ", strip=True)
            score = 9
        else:
            text = a.get_text(" ", strip=True)
            score = 6

        if len(text) < 28:
            continue
        if _CREDIT.search(text):
            continue

        c.add(text, href, score=score)

    return c.results()
