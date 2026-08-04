"""Fetch a site front page and turn it into headline pairs."""

from __future__ import annotations

import sys
from difflib import SequenceMatcher

from bs4 import BeautifulSoup

from parsers import extract_for_site
from parsers.base import normalize_for_dedup

from .fetch import fetch_html

def filter_by_length(candidates, min_len, max_len):
    return [c for c in candidates if min_len <= len(c[0]) <= max_len]


def fuzzy_dedupe(candidates, threshold=0.85):
    kept = []
    for text, score, href, pos in candidates:
        norm = normalize_for_dedup(text)
        is_dup = False
        for item in kept:
            kept_norm = item[3]
            shorter, longer = (norm, kept_norm) if len(norm) <= len(kept_norm) else (kept_norm, norm)
            if len(shorter) >= 20 and shorter in longer:
                is_dup = True
                item[2] = min(item[2], pos)
                break
            if SequenceMatcher(None, norm, kept_norm).ratio() >= threshold:
                is_dup = True
                item[2] = min(item[2], pos)
                break
        if not is_dup:
            kept.append([text, href, pos, norm])
    kept.sort(key=lambda item: item[2])
    return [(text, href, pos) for text, href, pos, _ in kept]


def merge_by_href(items):
    groups = {}
    for text, href, pos in items:
        if href:
            groups.setdefault(href, []).append((text, pos))

    merged = []
    for href, entries in groups.items():
        entries.sort(key=lambda e: e[1])
        parts, seen_norm = [], set()
        for text, _ in entries:
            key = normalize_for_dedup(text)
            if key in seen_norm:
                continue
            seen_norm.add(key)
            parts.append(text)
        combined_text = ", ".join(parts)
        min_pos = min(pos for _, pos in entries)
        merged.append((combined_text, href, min_pos))

    merged.sort(key=lambda item: item[2])
    return [(text, href) for text, href, _ in merged]

def _log_parser_diff(site_id, diff):
    if not diff:
        return
    if diff.get("missing_grammar"):
        print(
            f"[parser-engine=both] {site_id}: no grammar file (using Python)",
            file=sys.stderr,
        )
        return
    n_py = len(diff.get("only_py") or [])
    n_gr = len(diff.get("only_grammar") or [])
    n_score = len(diff.get("score_mismatch") or [])
    if diff.get("ok"):
        print(f"[parser-engine=both] {site_id}: OK (parity)", file=sys.stderr)
        return
    print(
        f"[parser-engine=both] {site_id}: DIFF "
        f"only_py={n_py} only_grammar={n_gr} score_mismatch={n_score}",
        file=sys.stderr,
    )


def extract_from_site(mod, min_len, max_len, limit=None, url=None, parser_engine=None):
    url = url or mod.DEFAULT_URL
    timeout = int(getattr(mod, "FETCH_TIMEOUT", 15) or 15)
    page_html = fetch_html(url, timeout=timeout)
    soup = BeautifulSoup(page_html, "html.parser")
    candidates, diff = extract_for_site(mod, soup, url, engine=parser_engine)
    _log_parser_diff(getattr(mod, "SITE_ID", "?"), diff)
    if not candidates:
        print(
            f"Warning: {getattr(mod, 'SITE_ID', '?')} returned 0 raw candidates — "
            f"check parsers/grammar/{getattr(mod, 'SITE_ID', 'site')}.yaml selectors",
            file=sys.stderr,
        )
    candidates = filter_by_length(candidates, min_len, max_len)
    candidates.sort(key=lambda c: (-c[1], len(c[0])))
    results = fuzzy_dedupe(candidates)
    results = merge_by_href(results)
    if limit:
        results = results[:limit]
    return results, url, page_html
