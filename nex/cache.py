"""Headline cache read/write and new-vs-seen splitting."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from .constants import (
    DEFAULT_CACHE_TTL_DAYS,
    MAX_CACHE_TTL_DAYS,
    MIN_CACHE_TTL_DAYS,
)

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
        parent = os.path.dirname(path or "")
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        print(f"Warning: couldn't write cache {path} ({e}).", file=sys.stderr)


def clamp_cache_ttl_days(value, default=None):
    """Return a life-span in days inside the supported range."""
    if default is None:
        default = DEFAULT_CACHE_TTL_DAYS
    try:
        days = int(value)
    except (TypeError, ValueError):
        return default
    return max(MIN_CACHE_TTL_DAYS, min(MAX_CACHE_TTL_DAYS, days))


def resolve_site_cache_ttl(settings, site_entry=None):
    """
    Life-span for one site. A per-site ``cache_ttl_days`` overrides the
    global setting. Missing or invalid values fall back to the default.
    """
    settings = settings if isinstance(settings, dict) else {}
    default = clamp_cache_ttl_days(settings.get("cache_ttl_days", DEFAULT_CACHE_TTL_DAYS))
    if isinstance(site_entry, dict):
        raw = site_entry.get("cache_ttl_days", None)
        if raw not in (None, ""):
            return clamp_cache_ttl_days(raw, default=default)
    return default


def _parse_iso(value):
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def coerce_href_entry(value):
    """
    Normalise one cached page.

    Legacy caches stored a title string. Newer entries are objects with
    title, first_seen, last_seen, and hits. Missing times stay None so the
    UI can say detection was not recorded yet.
    """
    if isinstance(value, dict):
        title = value.get("title")
        if title is None:
            title = value.get("text", "")
        hits = value.get("hits")
        try:
            hits = int(hits) if hits is not None and hits != "" else None
        except (TypeError, ValueError):
            hits = None
        if hits is not None and hits < 0:
            hits = None
        return {
            "title": "" if title is None else str(title),
            "first_seen": _parse_iso(value.get("first_seen")),
            "last_seen": _parse_iso(value.get("last_seen")),
            "hits": hits,
            "estimated": bool(value.get("estimated")),
        }
    return {
        "title": "" if value is None else str(value),
        "first_seen": None,
        "last_seen": None,
        "hits": None,
        "estimated": False,
    }


def _entry_for_json(entry):
    """Persist only known fields so untouched legacy rows stay compact."""
    out = {"title": entry.get("title") or ""}
    first_seen = entry.get("first_seen")
    last_seen = entry.get("last_seen")
    if isinstance(first_seen, datetime):
        out["first_seen"] = _iso(first_seen)
    if isinstance(last_seen, datetime):
        out["last_seen"] = _iso(last_seen)
    hits = entry.get("hits")
    if isinstance(hits, int) and hits >= 0:
        out["hits"] = hits
    if entry.get("estimated"):
        out["estimated"] = True
    return out


def _is_expired(entry, ttl_days, now):
    last_seen = entry.get("last_seen")
    if not isinstance(last_seen, datetime) or ttl_days is None:
        return False
    return now - last_seen > timedelta(days=ttl_days)


def href_title(value):
    """Title string from either a legacy cache value or a page object."""
    if isinstance(value, dict):
        title = value.get("title")
        if title is None:
            title = value.get("text", "")
        return "" if title is None else str(title)
    return "" if value is None else str(value)


def split_new_vs_seen(results, source_url, cache, ttl_days=None, now=None):
    """
    Split this front page into new vs previously seen, and refresh the cache.

    ``ttl_days`` is how long a page is remembered after it was last on the
    front page. Once that life-span passes, the page is forgotten and a later
    appearance is treated as newly detected. ``ttl_days`` None keeps every
    stored page (no expiry).
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    now_iso = _iso(now)

    domain = urlparse(source_url).netloc
    site_cache = cache.get(domain, {})
    if not isinstance(site_cache, dict):
        site_cache = {}
    raw_hrefs = site_cache.get("hrefs")
    if not isinstance(raw_hrefs, dict):
        raw_hrefs = {}

    # Pages stored before detection times existed have no last_seen. Anchor
    # them to the previous run so the upgrade does not wipe the cache, and
    # does not keep those URLs forever.
    imported_last_seen = _parse_iso(site_cache.get("last_run")) or now

    hrefs = {}
    for href, raw in raw_hrefs.items():
        entry = coerce_href_entry(raw)
        if entry["last_seen"] is None:
            entry["last_seen"] = imported_last_seen
            entry["estimated"] = True
        hrefs[str(href)] = entry

    ttl = None if ttl_days is None else clamp_cache_ttl_days(ttl_days)
    if ttl is not None:
        for href in list(hrefs):
            if _is_expired(hrefs[href], ttl, now):
                del hrefs[href]

    had_baseline = bool(raw_hrefs)
    is_first_run = not had_baseline
    previously_seen = set(hrefs)

    new_results, seen_results = [], []
    for text, href in results:
        if href in previously_seen:
            seen_results.append((text, href))
        else:
            new_results.append((text, href))

    for text, href in results:
        entry = hrefs.get(href)
        if entry is None:
            hrefs[href] = {
                "title": text,
                "first_seen": now,
                "last_seen": now,
                "hits": 1,
            }
        else:
            entry["title"] = text
            entry["last_seen"] = now
            entry["estimated"] = False
            if entry["hits"] is None:
                entry["hits"] = 1
            else:
                entry["hits"] = int(entry["hits"]) + 1

    if is_first_run:
        display_new, display_seen = [], list(results)
    else:
        display_new, display_seen = list(new_results), list(seen_results)

    cache[domain] = {
        "hrefs": {href: _entry_for_json(entry) for href, entry in hrefs.items()},
        "last_run": now_iso,
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
    results = [(href_title(text), str(href)) for href, text in hrefs.items()]
    if limit is not None:
        results = results[:limit]
    return [], results, False, True


def _pages_from_site_cache(site_cache):
    """Compact page rows: [title, href, first_seen, last_seen, hits, on_page]."""
    hrefs = site_cache.get("hrefs") if isinstance(site_cache, dict) else None
    if not isinstance(hrefs, dict):
        return []
    on_page = set()
    display = site_cache.get("last_display") if isinstance(site_cache, dict) else None
    if isinstance(display, dict):
        for bucket in ("new", "seen"):
            for _text, href in _pairs_from_cache_list(display.get(bucket)):
                on_page.add(href)
    pages = []
    for href, raw in hrefs.items():
        entry = coerce_href_entry(raw)
        first_seen = _iso(entry["first_seen"]) if entry["first_seen"] else ""
        last_seen = _iso(entry["last_seen"]) if entry["last_seen"] else ""
        pages.append([
            entry["title"],
            str(href),
            first_seen,
            last_seen,
            entry["hits"],
            1 if str(href) in on_page else 0,
            1 if entry.get("estimated") else 0,
        ])
    pages.sort(key=lambda row: row[3] or "", reverse=True)
    pages.sort(key=lambda row: 0 if row[5] else 1)
    return pages


def snapshot_page_cache(cache, site_blocks, sites_config=None):
    """
    Group the page cache by site for the developer view.

    Each page row is
    ``[title, href, first_seen, last_seen, hits, on_page]``.
    ``first_seen`` / ``last_seen`` are ISO strings, or "" when not recorded.
    ``hits`` is an int, or null when the count is unknown.
    ``on_page`` is 1 when the URL is on the latest front-page snapshot.
    """
    if not isinstance(cache, dict):
        cache = {}
    sites_config = sites_config if isinstance(sites_config, dict) else {}
    claimed = set()
    sites = []

    for block in site_blocks or []:
        if not isinstance(block, dict):
            continue
        sid = str(block.get("site_id") or "")
        url = block.get("url") or ""
        entry_cfg = sites_config.get(sid, {})
        raw_ttl = entry_cfg.get("cache_ttl_days") if isinstance(entry_cfg, dict) else None
        ttl = None
        if raw_ttl not in (None, ""):
            ttl = clamp_cache_ttl_days(raw_ttl)
        key, site_cache = find_site_cache(cache, url)
        pages = []
        last_run = None
        if isinstance(site_cache, dict) and key and key not in claimed:
            claimed.add(key)
            pages = _pages_from_site_cache(site_cache)
            if isinstance(site_cache.get("last_run"), str):
                last_run = site_cache.get("last_run")
        sites.append({
            "id": sid,
            "name": block.get("name") or sid,
            "domain": key or "",
            "last_run": last_run,
            "ttl": ttl,
            "pages": pages,
        })

    for key, site_cache in cache.items():
        if key in claimed or not isinstance(site_cache, dict):
            continue
        pages = _pages_from_site_cache(site_cache)
        if not pages:
            continue
        last_run = site_cache.get("last_run") if isinstance(site_cache.get("last_run"), str) else None
        sites.append({
            "id": "",
            "name": str(key),
            "domain": str(key),
            "last_run": last_run,
            "ttl": None,
            "orphan": True,
            "pages": pages,
        })
    return {"sites": sites}
