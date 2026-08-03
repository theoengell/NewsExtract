"""Headline extractor for politiken.dk (article teasers with /artNNNN links)."""

from __future__ import annotations

import re

from .base import CandidateCollector

DOMAINS = ("politiken.dk",)
NAME = "Politiken"
LANGUAGE = "da"
DEFAULT_URL = "https://politiken.dk/"

_ART_HREF = re.compile(r"/art\d+", re.IGNORECASE)
_SKIP_PATH = re.compile(r"/om_politiken/|/shop/|/tag/", re.IGNORECASE)


def extract(soup, base_url: str):
    """Prefer article cards whose links point at /artNNNN story URLs."""
    c = CandidateCollector(base_url)

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not _ART_HREF.search(href):
            continue
        if _SKIP_PATH.search(href):
            continue

        heading = a.find(["h1", "h2", "h3", "h4", "h5"])
        if heading:
            text = heading.get_text(" ", strip=True)
            score = 9
        else:
            # Parent article may hold the heading next to the link.
            article = a.find_parent("article")
            if article:
                heading = article.find(["h1", "h2", "h3", "h4", "h5"])
                text = heading.get_text(" ", strip=True) if heading else a.get_text(" ", strip=True)
                score = 8 if heading else 5
            else:
                text = a.get_text(" ", strip=True)
                score = 5

        if len(text) < 12:
            continue
        c.add(text, href, score=score)

    return c.results()
