"""Shared helpers for site-specific headline parsers."""

from __future__ import annotations

import re
from urllib.parse import urljoin

# Words/phrases that are almost never real headlines - nav, boilerplate, etc.
NOISE_PATTERNS = [
    r"^log ?ind$", r"^log ?in$", r"^sign ?in$", r"^sign ?up$",
    r"^menu$", r"^s[oø]g$", r"^search$", r"^abonner$", r"^subscribe$",
    r"^cookies?$", r"^accepter$", r"^luk$", r"^close$", r"^se mere$",
    r"^read more$", r"^next$", r"^previous$", r"^forrige$", r"^n[aæ]ste$",
    r"^del$", r"^share$", r"^kontakt$", r"^contact$", r"^om os$",
    r"^about$", r"^privacy", r"^persondata", r"^cookiepolitik",
    r"^annonce$", r"^advert", r"^\d{1,2}:\d{2}$",
    r"^l i v e\b", r"^live\b.*(lige nu\s*){2,}", r"^(lige nu\s*){2,}$",
    r"^pr[oø]v .*(abonn|plus|\+)", r".*(abonn[eè]r|k[oø]b).*\+.*for \d+ ?kr",
]
NOISE_RE = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

# Tags/classes/attrs that strongly suggest "this is a headline"
HEADLINE_CLASS_HINTS = re.compile(
    r"(headline|title|teaser|artikel|article|overskrift|heading)", re.IGNORECASE
)

# Fluid typography sometimes glues words ("landetRamt", "advarer:Ny").
_CAMEL_GAP = re.compile(r"([a-zæøå])([A-ZÆØÅ])")
_PUNCT_GAP = re.compile(r"([:!?])(\S)")
_HYPHEN_LINEBREAK = re.compile(r"(\S)-\s+(\S)")


def clean_glued_headline(text: str) -> str:
    """
    Insert missing spaces where line-broken / fluid headlines were glued:
    camelCase boundaries, punctuation before a word, then normalise spaces.
    Also re-joins 'word- word' hyphenations caused by a break after '-'.
    """
    text = (text or "").replace("\xa0", " ").replace("\u00ad", "")
    text = _CAMEL_GAP.sub(r"\1 \2", text)
    text = _PUNCT_GAP.sub(r"\1 \2", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = _HYPHEN_LINEBREAK.sub(r"\1-\2", text)
    return text


def teaserlink_headline(link_el) -> str:
    """
    BT / Berlingske TeaserLink cards split the title across fluid-line spans.
    Their aria-label often concatenates those lines without spaces. Rebuild
    the title by joining each visible fluid line with a space.
    """
    box = link_el.find_parent(class_=re.compile(r"TeaserLink_container"))
    if box is None:
        return clean_glued_headline(link_el.get("aria-label") or "")

    hl = box.select_one("[class*='TeaserHeadline_container']")
    if hl is None:
        return clean_glued_headline(link_el.get("aria-label") or "")

    parts = []
    for line in hl.select("[class*='TeaserHeadline_fluidLine']"):
        if line.find_parent(attrs={"aria-hidden": "true"}):
            continue
        t = line.get_text(" ", strip=True)
        if t and (not parts or parts[-1] != t):
            parts.append(t)

    if parts:
        return clean_glued_headline(" ".join(parts))
    return clean_glued_headline(link_el.get("aria-label") or "")


def is_probably_noise(text: str) -> bool:
    t = text.strip()
    if not t:
        return True
    if NOISE_RE.search(t):
        return True
    if t.isdigit():
        return True
    return False


def collapse_repeated_phrase(text: str) -> str:
    """
    Some sites duplicate a headline's text 2+ times inside the same element
    (marquee/ticker, hover/mobile duplicate). Collapse to one instance of
    the smallest repeating word-sequence.
    """
    words = text.split()
    n = len(words)
    if n < 2:
        return text
    for k in range(1, n // 2 + 1):
        if n % k != 0:
            continue
        if all(words[i] == words[i % k] for i in range(n)):
            return " ".join(words[:k])
    return text


def normalize_for_dedup(text: str) -> str:
    """Normalize text so near-duplicates collapse to the same key."""
    t = text.lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def normalize_domain(netloc: str) -> str:
    """Lowercase host and strip a leading www."""
    host = (netloc or "").lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


class CandidateCollector:
    """
    Collects (text, score, href, pos) candidates with exact-key dedup.
    Site parsers call add(); call results() at the end.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._candidates = {}
        self._position = 0

    def add(self, text, href, score):
        pos = self._position
        self._position += 1
        text = clean_glued_headline(text or "")
        text = collapse_repeated_phrase(text)
        if not text or is_probably_noise(text):
            return
        key = normalize_for_dedup(text)
        if not key:
            return
        full_href = urljoin(self.base_url, href) if href else None
        if key not in self._candidates:
            self._candidates[key] = [text, score, full_href, pos]
        else:
            existing = self._candidates[key]
            existing[3] = min(existing[3], pos)
            if score > existing[1] or (score == existing[1] and len(text) < len(existing[0])):
                existing[0], existing[1] = text, score
                if full_href:
                    existing[2] = full_href

    def results(self):
        return [tuple(v) for v in self._candidates.values()]
