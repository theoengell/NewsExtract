"""Headline cache read/write and new-vs-seen splitting."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

from parsers.base import normalize_domain

def load_cache(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: couldn't read cache {path} ({e}), starting fresh.", file=sys.stderr)
        return {}


def save_cache(path, cache):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        print(f"Warning: couldn't write cache {path} ({e}).", file=sys.stderr)

def split_new_vs_seen(results, source_url, cache):
    domain = urlparse(source_url).netloc
    site_cache = cache.get(domain, {})
    previously_seen_hrefs = set(site_cache.get("hrefs", {}).keys())
    is_first_run = not previously_seen_hrefs

    new_results, seen_results = [], []
    for text, href in results:
        if href in previously_seen_hrefs:
            seen_results.append((text, href))
        else:
            new_results.append((text, href))

    all_hrefs = dict(site_cache.get("hrefs", {}))
    for text, href in results:
        all_hrefs[href] = text

    if is_first_run:
        display_new, display_seen = [], list(results)
    else:
        display_new, display_seen = list(new_results), list(seen_results)

    cache[domain] = {
        "hrefs": all_hrefs,
        "last_run": datetime.now(timezone.utc).isoformat(),
        "last_display": {
            "new": [[text, href] for text, href in display_new],
            "seen": [[text, href] for text, href in display_seen],
            "is_first_run": is_first_run,
        },
    }

    if is_first_run:
        return [], results, True
    return new_results, seen_results, False


def _pairs_from_cache_list(items):
    pairs = []
    if not isinstance(items, list):
        return pairs
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            pairs.append((str(item[0]), str(item[1])))
    return pairs


def find_site_cache(cache, source_url):
    """Look up a site's cache entry by URL host, trying www / non-www variants."""
    netloc = urlparse(source_url).netloc
    if not netloc:
        return None, None
    candidates = [netloc]
    if netloc.startswith("www."):
        candidates.append(netloc[4:])
    else:
        candidates.append("www." + netloc)
    for key in candidates:
        entry = cache.get(key)
        if isinstance(entry, dict):
            return key, entry
    return netloc, None


def load_results_from_cache(cache, source_url, limit=None):
    """
    Rebuild new/seen headline lists from the last --update snapshot.
    Returns (new_results, seen_results, is_first_run, ok).
    """
    _key, site_cache = find_site_cache(cache, source_url)
    if not site_cache:
        return [], [], True, False

    display = site_cache.get("last_display")
    if isinstance(display, dict):
        new_results = _pairs_from_cache_list(display.get("new"))
        seen_results = _pairs_from_cache_list(display.get("seen"))
        is_first_run = bool(display.get("is_first_run"))
        if limit is not None:
            if is_first_run:
                seen_results = seen_results[:limit]
            else:
                # Keep new first, then fill remaining slots from seen.
                new_results = new_results[:limit]
                remaining = max(0, limit - len(new_results))
                seen_results = seen_results[:remaining]
        return new_results, seen_results, is_first_run, True

    # Older caches without last_display: treat all stored hrefs as previously seen.
    hrefs = site_cache.get("hrefs")
    if not isinstance(hrefs, dict) or not hrefs:
        return [], [], True, False
    results = [(str(text), str(href)) for href, text in hrefs.items()]
    if limit is not None:
        results = results[:limit]
    return [], results, False, True
