"""Headline extractor for TV 2 Nyheder (tc_teaser cards)."""

from __future__ import annotations

from .base import CandidateCollector

DOMAINS = ("nyheder.tv2.dk", "tv2.dk")
NAME = "TV 2 Nyheder"
LANGUAGE = "da"
DEFAULT_URL = "https://nyheder.tv2.dk/"


def extract(soup, base_url: str):
    """Use .tc_teaser cards: heading text + teaser link."""
    c = CandidateCollector(base_url)

    for teaser in soup.select(".tc_teaser"):
        link = teaser.select_one("a.tc_teaser__link[href]") or teaser.find("a", href=True)
        heading = (
            teaser.select_one("h2.tc_heading, h3.tc_heading, h4.tc_heading")
            or teaser.select_one(".tc_heading")
        )
        if not link or not heading:
            continue
        text = heading.get_text(" ", strip=True)
        c.add(text, link.get("href"), score=9)

    return c.results()
