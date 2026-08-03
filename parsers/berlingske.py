"""Headline extractor for b.dk / berlingske.dk (TeaserLink fluid-line titles)."""

from __future__ import annotations

from .base import CandidateCollector, teaserlink_headline

DOMAINS = ("b.dk", "berlingske.dk")
NAME = "Berlingske"
LANGUAGE = "da"
DEFAULT_URL = "https://www.b.dk/"


def extract(soup, base_url: str):
    """
    Berlingske uses the same TeaserLink fluid-line layout as BT.
    Rebuild titles from spans so line breaks become spaces.
    """
    c = CandidateCollector(base_url)

    for a in soup.select("a[class*='TeaserLink_link'][href]"):
        href = a.get("href") or ""
        if "/video/reels/" in href:
            continue
        text = teaserlink_headline(a)
        if not text:
            continue
        c.add(text, href, score=9)

    return c.results()
