"""Headline extractor for weekendavisen.dk (TeaserLink fluid-line titles)."""

from __future__ import annotations

from .base import CandidateCollector, teaserlink_headline

DOMAINS = ("weekendavisen.dk",)
NAME = "Weekendavisen"
LANGUAGE = "da"
DEFAULT_URL = "https://www.weekendavisen.dk/"


def extract(soup, base_url: str):
    """
    Weekendavisen uses the same TeaserLink fluid-line layout as BT/Berlingske.
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
