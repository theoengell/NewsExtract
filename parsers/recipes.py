"""Allowlisted title/link transforms used by grammar strategies."""

from __future__ import annotations

import re

from .base import teaserlink_headline

_DCR_QUOTE = re.compile(
    r"\b(?:double|single)\s+quotation\s+mark\b",
    re.IGNORECASE,
)


def text(el, _ctx=None) -> str:
    if el is None:
        return ""
    return el.get_text(" ", strip=True) or ""


def aria_label(el, _ctx=None) -> str:
    if el is None:
        return ""
    return (el.get("aria-label") or "").strip()


def text_or_aria(el, _ctx=None) -> str:
    """Link text, falling back to aria-label (Guardian-style)."""
    if el is None:
        return ""
    return text(el) or aria_label(el)


def teaserlink_fluid_lines(el, _ctx=None) -> str:
    if el is None:
        return ""
    return teaserlink_headline(el) or ""


def nested_heading(el, _ctx=None) -> str:
    if el is None:
        return ""
    heading = el.find(["h1", "h2", "h3", "h4", "h5"])
    if heading is None:
        return ""
    return heading.get_text(" ", strip=True) or ""


def first_comma_chunk(value, _ctx=None) -> str:
    """If value is a long string containing ', ', keep the first segment (>90)."""
    text_val = value if isinstance(value, str) else text(value)
    threshold = 90
    if isinstance(_ctx, dict) and _ctx.get("comma_min") is not None:
        threshold = int(_ctx["comma_min"])
    if ", " in text_val and len(text_val) > threshold:
        return text_val.split(", ", 1)[0].strip()
    return text_val


def first_comma_chunk_100(value, _ctx=None) -> str:
    """Guardian pass-1 threshold: split only when length > 100."""
    return first_comma_chunk(value, {"comma_min": 100})


def strip_dcr_quotes(value, _ctx=None) -> str:
    text_val = value if isinstance(value, str) else text(value)
    text_val = _DCR_QUOTE.sub("", text_val or "")
    return re.sub(r"\s+", " ", text_val).strip(" -–—|")


def score_by_heading_level(el, _ctx=None) -> int:
    """Map h1→10 … h4→7; unknown → 5."""
    if el is None or not getattr(el, "name", None):
        return 5
    name = el.name.lower()
    if len(name) == 2 and name[0] == "h" and name[1].isdigit():
        level = int(name[1])
        return 10 - (level - 1)
    return 5


RECIPES = {
    "text": text,
    "aria_label": aria_label,
    "text_or_aria": text_or_aria,
    "teaserlink_fluid_lines": teaserlink_fluid_lines,
    "nested_heading": nested_heading,
    "first_comma_chunk": first_comma_chunk,
    "first_comma_chunk_100": first_comma_chunk_100,
    "strip_dcr_quotes": strip_dcr_quotes,
    "score_by_heading_level": score_by_heading_level,
}


def get_recipe(name: str):
    if name not in RECIPES:
        known = ", ".join(sorted(RECIPES))
        raise KeyError(f"Unknown recipe '{name}'. Known: {known}")
    return RECIPES[name]
