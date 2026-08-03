"""Headline extractor for ekstrabladet.dk (heuristic heading/link scan)."""

from __future__ import annotations

from .base import HEADLINE_CLASS_HINTS, CandidateCollector

DOMAINS = ("ekstrabladet.dk",)
NAME = "Ekstra Bladet"
LANGUAGE = "da"
DEFAULT_URL = "https://ekstrabladet.dk/"


def extract(soup, base_url: str):
    """
    Returns a list of (text, score, href, pos) candidates for headlines.
    Broad heuristic that works well on ekstrabladet.dk's mixed markup.
    """
    c = CandidateCollector(base_url)

    for el in soup.find_all(["h1", "h2", "h3", "h4", "a"]):
        if el.name in ("h1", "h2", "h3", "h4"):
            level = int(el.name[1]) - 1
            text = el.get_text(" ", strip=True)
            link = el.find("a")
            href = link.get("href") if link else None
            if href is None:
                parent_link = el.find_parent("a")
                href = parent_link.get("href") if parent_link else None
            c.add(text, href, score=10 - level)
        else:
            if not el.get("href"):
                continue
            classes = " ".join(el.get("class", [])) + " " + (el.get("id") or "")
            text = el.get_text(" ", strip=True)
            if HEADLINE_CLASS_HINTS.search(classes):
                c.add(text, el["href"], score=6)
            elif len(text) >= 25:
                c.add(text, el["href"], score=3)

    return c.results()
