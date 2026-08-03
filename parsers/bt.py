"""Headline extractor for bt.dk (TeaserLink fluid-line titles)."""

from __future__ import annotations

from .base import CandidateCollector, teaserlink_headline

DOMAINS = ("bt.dk",)
NAME = "BT"
LANGUAGE = "da"
DEFAULT_URL = "https://www.bt.dk/"


def extract(soup, base_url: str):
    """
    BT splits headline glyphs across fluid-line spans for CSS text-fit.
    aria-label often glues those lines together; rebuild from the spans.
    """
    c = CandidateCollector(base_url)

    for a in soup.select("a[class*='TeaserLink_link'][href]"):
        href = a.get("href") or ""
        # Skip short-form video reels; keep article teasers.
        if "/video/reels/" in href or href.rstrip("/").endswith("/video"):
            continue
        text = teaserlink_headline(a)
        if not text:
            continue
        c.add(text, href, score=9)

    return c.results()
