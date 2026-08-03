"""Headline extractor for dr.dk (Hydra card layout)."""

from __future__ import annotations

from .base import CandidateCollector

DOMAINS = ("dr.dk",)
NAME = "DR"
LANGUAGE = "da"
DEFAULT_URL = "https://www.dr.dk/"


def extract(soup, base_url: str):
    """Pull titles from hydra front-page cards / teaser title links."""
    c = CandidateCollector(base_url)

    for card in soup.select(".hydra-card, .hydra-front-page-item"):
        title_el = (
            card.select_one(".hydra-card-title__text")
            or card.select_one(".hydra-card-title")
            or card.select_one(".hydra-teaser-title")
        )
        link = card.select_one("a.hydra-teaser-title[href]") or card.find("a", href=True)
        if not title_el or not link:
            continue
        text = title_el.get_text(" ", strip=True)
        c.add(text, link.get("href"), score=9)

    # Fallback: standalone teaser title links not inside a card we already got
    for a in soup.select("a.hydra-teaser-title[href]"):
        text = a.get_text(" ", strip=True)
        c.add(text, a.get("href"), score=7)

    return c.results()
