#!/usr/bin/env python3
"""
newsextract.py

Discovers site parsers in the parsers/ folder. By default rebuilds the HTML
dashboard from the local cache (no network). Pass --update to fetch each
enabled site's front page and refresh the cache.

No URL arguments needed - add a grammar under parsers/grammar/. Toggle sites in
sites.json, UI defaults in settings.json, and categories in categories.json
(or on the HTML page). Unmatched URL sections are auto-added to categories.json
on each run.

Usage:
    python newsextract.py
    python newsextract.py --update
    python newsextract.py --update -v
    python newsextract.py --list-sites
    python newsextract.py --limit 20 -o headlines.txt
    python newsextract.py --no-html
    python newsextract.py --only ekstrabladet,dr

Install dependencies first:
    pip install requests beautifulsoup4 --break-system-packages
"""

import argparse
import html
import json
import os
import re
import sys
import time
import webbrowser
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from parsers import (
    PARSERS,
    UnsupportedSiteError,
    extract_for_site,
    get_parser_by_id,
    list_sites,
    refresh,
)
from parsers import PARSER_ENGINE as _DEFAULT_PARSER_ENGINE
import parsers as parsers_pkg
from parsers.base import normalize_domain, normalize_for_dedup

DEFAULT_CACHE_FILE = "headlines_cache.json"
DEFAULT_CATEGORIES_FILE = "categories.json"
LEGACY_COLOR_MAP_FILE = "url_colors.json"
DEFAULT_SITES_FILE = "sites.json"
DEFAULT_SETTINGS_FILE = "settings.json"
DEFAULT_HTML_FILE = "headlines.html"
DEFAULT_LOGOS_DIR = "logos"


def make_default_settings():
    return {
        "show_external": True,
        "only_new": False,
        "bg_strength": 35,
        "seen_limit": 15,
        "highlight_words": "",
        "exclude_words": "",
        "site_order": [],
        "show_all_new": True,
        "dim_opened": True,
        "show_opened_today": True,
        "dark_mode": False,
        "languages": {"da": True, "en": True},
    }

# id -> {label, color, match: [url substrings], enabled}
DEFAULT_CATEGORIES = {
    "nyheder": {
        "label": "Nyheder",
        "color": "#e3f2fd",
        "match": ["/nyheder/", "/seneste/"],
        "enabled": True,
    },
    "indland": {
        "label": "Indland",
        "color": "#80cbc4",
        "match": ["/indland/", "/danmark/", "/samfund/"],
        "enabled": True,
    },
    "udland": {
        "label": "Udland",
        "color": "#ce93d8",
        "match": ["/udland/", "/internationalt/"],
        "enabled": True,
    },
    "politik": {
        "label": "Politik",
        "color": "#90caf9",
        "match": ["/politik/", "/danskpolitik/"],
        "enabled": True,
    },
    "krimi": {
        "label": "Krimi",
        "color": "#ef9a9a",
        "match": ["/krimi/"],
        "enabled": True,
    },
    "sport": {
        "label": "Sport",
        "color": "#a5d6a7",
        "match": ["/sport/", "/sporten/", "/fodbold/", "/cykling/", "/anden_sport/", "/superligaen/"],
        "enabled": True,
    },
    "penge": {
        "label": "Penge",
        "color": "#fff59d",
        "match": ["/penge/", "/virksomheder/", "/okonomi/", "/økonomi/"],
        "enabled": True,
    },
    "forbrug": {
        "label": "Forbrug",
        "color": "#ffcc80",
        "match": ["/forbrug/", "/sundhed/", "/vores-liv/"],
        "enabled": True,
    },
    "kultur": {
        "label": "Kultur",
        "color": "#b39ddb",
        "match": ["/kultur/", "/musik/", "/filmogtv/", "/film_og_tv/"],
        "enabled": True,
    },
    "underholdning": {
        "label": "Underholdning",
        "color": "#f8bbd0",
        "match": ["/underholdning/", "/royale/", "/dkkendte/", "/udlandkendte/", "/int-kendte/", "/side9/"],
        "enabled": True,
    },
    "debat": {
        "label": "Debat",
        "color": "#bcaaa4",
        "match": ["/debat/", "/debatindlaeg/", "/ledere/", "/kommentatorer/"],
        "enabled": True,
    },
    "live": {
        "label": "Live",
        "color": "#ffab91",
        "match": ["/live/"],
        "enabled": True,
    },
    "vejret": {
        "label": "Vejr & trafik",
        "color": "#b2ebf2",
        "match": ["/vejret/", "/trafik/"],
        "enabled": True,
    },
    "other": {
        "label": "Other",
        "color": "#eeeeee",
        "match": [],
        "enabled": True,
    },
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da,en-US;q=0.9,en;q=0.8",
}


def fetch_html(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    # When Content-Type has no charset, requests assumes ISO-8859-1. Sites like
    # TV 2 serve UTF-8 without declaring it, which mojibakes æ/ø/å.
    content_type = resp.headers.get("Content-Type", "")
    if "charset=" not in content_type.lower():
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


_ICON_REL_SCORE = {
    "apple-touch-icon": 100,
    "apple-touch-icon-precomposed": 95,
    "icon": 50,
    "shortcut icon": 40,
    "mask-icon": 20,
}


def discover_logo_url(soup, base_url):
    """Pick the best favicon / touch-icon URL from page <link> tags."""
    candidates = []
    for link in soup.find_all("link", href=True):
        rels = [r.lower() for r in (link.get("rel") or [])]
        if not rels:
            continue
        rel_joined = " ".join(rels)
        score = 0
        for key, value in _ICON_REL_SCORE.items():
            if key in rel_joined or key in rels:
                score = max(score, value)
        if score <= 0:
            continue
        href = (link.get("href") or "").strip()
        if not href or href.startswith("data:"):
            continue
        sizes = (link.get("sizes") or "").lower()
        m = re.search(r"(\d+)", sizes)
        if m:
            score += min(int(m.group(1)), 192)
        elif "svg" in (link.get("type") or "").lower() or href.lower().endswith(".svg"):
            score += 64
        candidates.append((score, urljoin(base_url, href)))

    if candidates:
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return candidates[0][1]

    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    return None


def _logo_ext_from_response(url, content_type, content):
    ct = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    if ct in mapping:
        return mapping[ct]
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if content[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if content.lstrip().startswith((b"<svg", b"<?xml")):
        return ".svg"
    return ".ico"


def _existing_logo_path(logos_dir, site_id):
    if not os.path.isdir(logos_dir):
        return None
    prefix = site_id + "."
    for name in sorted(os.listdir(logos_dir)):
        if name.startswith(prefix) and not name.endswith(".tmp"):
            return os.path.join(logos_dir, name)
    return None


def download_logo_bytes(logo_url, timeout=12, max_bytes=512_000):
    resp = requests.get(logo_url, headers=DEFAULT_HEADERS, timeout=timeout, stream=True)
    resp.raise_for_status()
    chunks = []
    total = 0
    for chunk in resp.iter_content(8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"logo too large (>{max_bytes} bytes)")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("empty logo response")
    return data, resp.headers.get("Content-Type", "")


def ensure_site_logo(site_id, site_url, logos_dir=DEFAULT_LOGOS_DIR, page_html=None, refresh=False):
    """
    Ensure logos/<site_id>.* exists. Returns a path relative to the HTML file
    (e.g. 'logos/dr.png'), or None if unavailable.
    """
    os.makedirs(logos_dir, exist_ok=True)
    existing = _existing_logo_path(logos_dir, site_id)
    if existing and not refresh:
        return os.path.join(logos_dir, os.path.basename(existing)).replace("\\", "/")

    soup = None
    if page_html:
        soup = BeautifulSoup(page_html, "html.parser")
    else:
        try:
            page_html = fetch_html(site_url, timeout=12)
            soup = BeautifulSoup(page_html, "html.parser")
        except (requests.RequestException, OSError):
            soup = None

    candidates = []
    if soup is not None:
        found = discover_logo_url(soup, site_url)
        if found:
            candidates.append(found)
    parsed = urlparse(site_url)
    if parsed.scheme and parsed.netloc:
        favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        if favicon not in candidates:
            candidates.append(favicon)
        domain = normalize_domain(parsed.netloc)
        if domain:
            candidates.append(f"https://www.google.com/s2/favicons?domain={domain}&sz=64")

    last_error = None
    for logo_url in candidates:
        try:
            data, content_type = download_logo_bytes(logo_url)
            ext = _logo_ext_from_response(logo_url, content_type, data)
            # Drop prior files for this site (extension may have changed).
            for name in list(os.listdir(logos_dir)):
                if name.startswith(site_id + ".") and not name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(logos_dir, name))
                    except OSError:
                        pass
            dest = os.path.join(logos_dir, site_id + ext)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            rel = os.path.join(logos_dir, site_id + ext).replace("\\", "/")
            print(f"Downloaded logo for {site_id} → {rel}", file=sys.stderr)
            return rel
        except (requests.RequestException, OSError, ValueError) as e:
            last_error = e
            continue

    if existing:
        return os.path.join(logos_dir, os.path.basename(existing)).replace("\\", "/")
    if last_error:
        print(f"Warning: no logo for {site_id} ({last_error})", file=sys.stderr)
    return None


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


def _normalize_category_entry(cat_id, raw, fallback=None):
    """Coerce a category entry into {label, color, match, enabled}."""
    fb = fallback or DEFAULT_CATEGORIES.get(cat_id, {})
    if isinstance(raw, str):
        # Legacy url_colors.json style: "/sport/": "#a5d6a7"
        return {
            "label": cat_id.strip("/").replace("-", " ").title() or cat_id,
            "color": raw,
            "match": [cat_id] if cat_id.startswith("/") else [f"/{cat_id}/"],
            "enabled": True,
        }
    if not isinstance(raw, dict):
        raw = {}
    match = raw.get("match", fb.get("match", []))
    if isinstance(match, str):
        match = [match]
    return {
        "label": raw.get("label") or fb.get("label") or cat_id,
        "color": raw.get("color") or fb.get("color") or "#eeeeee",
        "match": list(match),
        "enabled": bool(raw.get("enabled", fb.get("enabled", True))),
    }


def load_categories(path):
    """
    Load categories.json. Migrates legacy url_colors.json if present and the
    new file is missing. Ensures DEFAULT_CATEGORIES keys exist.
    """
    data = None
    source = path

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: couldn't read {path} ({e}), using defaults.", file=sys.stderr)
    elif os.path.exists(LEGACY_COLOR_MAP_FILE):
        try:
            with open(LEGACY_COLOR_MAP_FILE, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            if isinstance(legacy, dict):
                # Convert "/sport/": "#hex" into category entries, then merge defaults.
                data = {}
                for key, color in legacy.items():
                    slug = key.strip("/").split("/")[0] or key
                    data[slug] = _normalize_category_entry(key, color)
                print(
                    f"Migrating {LEGACY_COLOR_MAP_FILE} -> {path}. "
                    f"You can delete the old file.",
                    file=sys.stderr,
                )
                source = LEGACY_COLOR_MAP_FILE
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: couldn't read {LEGACY_COLOR_MAP_FILE} ({e}).", file=sys.stderr)

    categories = {}
    if isinstance(data, dict):
        for cat_id, raw in data.items():
            categories[cat_id] = _normalize_category_entry(cat_id, raw)

    # Ensure defaults exist (new categories added in code show up on disk).
    changed = False
    for cat_id, meta in DEFAULT_CATEGORIES.items():
        if cat_id not in categories:
            categories[cat_id] = dict(meta)
            categories[cat_id]["match"] = list(meta["match"])
            changed = True
        else:
            # Keep user enabled/color/label/match; only fill missing fields.
            for field in ("label", "color", "match", "enabled"):
                if field not in categories[cat_id] or categories[cat_id][field] in (None, []):
                    if field == "match" and categories[cat_id].get("match") == [] and cat_id != "other":
                        continue
                    if categories[cat_id].get(field) in (None,):
                        categories[cat_id][field] = list(meta[field]) if field == "match" else meta[field]
                        changed = True

    if "other" not in categories:
        categories["other"] = dict(DEFAULT_CATEGORIES["other"])
        changed = True

    if changed or not os.path.exists(path) or source == LEGACY_COLOR_MAP_FILE:
        save_categories(path, categories)
        print(f"Updated {path}.", file=sys.stderr)

    return categories


def save_categories(path, categories):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(categories, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        print(f"Warning: couldn't write {path} ({e}).", file=sys.stderr)


def match_category(href, categories):
    """
    Return (category_id, meta) for a URL. Prefers the longest matching
    substring; on a tie, the match that appears later in the path wins
    (e.g. /indland/ over /nyheder/ in .../nyheder/indland/...).
    Falls back to 'other'.
    """
    href_l = (href or "").lower()
    best_id, best_meta = None, None
    best_len, best_pos = -1, -1
    for cat_id, meta in categories.items():
        if cat_id == "other":
            continue
        for needle in meta.get("match") or []:
            if not needle:
                continue
            n = needle.lower()
            pos = href_l.find(n)
            if pos < 0:
                continue
            score_len = len(n)
            if score_len > best_len or (score_len == best_len and pos > best_pos):
                best_id, best_meta = cat_id, meta
                best_len, best_pos = score_len, pos
    if best_id is not None:
        return best_id, best_meta
    other = categories.get("other") or DEFAULT_CATEGORIES["other"]
    return "other", other


def get_bg_color(href, categories):
    _cat_id, meta = match_category(href, categories)
    return meta.get("color")


# Palette for auto-discovered categories (cycled as new ones appear).
AUTO_CATEGORY_COLORS = [
    "#c5e1a5", "#ffe082", "#b3e5fc", "#d1c4e9", "#ffcdd2",
    "#b2dfdb", "#f0f4c3", "#ffccbc", "#d7ccc8", "#e1bee7",
]

# Path segments that are almost never useful category labels.
_SKIP_SEGMENTS = {
    "www", "index", "html", "htm", "tag", "tags", "shop", "om", "about",
    "cookie", "cookies", "search", "video", "reels", "page", "pages",
    "artikel", "article", "articles", "story", "stories", "content",
    "static", "assets", "images", "img", "css", "js", "api", "rss",
    "feed", "author", "authors", "user", "users", "login", "logout",
    "main", "home", "forside", "preview", "amp", "print", "node",
    "media", "files", "wp-content", "wp-json", "category", "categories",
}

_ARTICLE_SEG = re.compile(r"^(art|article)\d+", re.I)
_LONG_ID = re.compile(r"\d{5,}")


def _path_section_candidates(href):
    """
    Return the first plausible section slug from the URL path.
    Only the top-level section is used so article title slugs are ignored.
    """
    path = urlparse(href or "").path or ""
    parts = [p for p in path.split("/") if p]
    for p in parts[:2]:
        seg = p.lower()
        if seg in _SKIP_SEGMENTS:
            continue
        if seg.isdigit() or len(seg) < 3 or len(seg) > 40:
            continue
        if _ARTICLE_SEG.match(seg) or _LONG_ID.search(seg):
            continue
        # Likely an article title slug, not a section.
        if seg.count("-") >= 3 and len(seg) > 20:
            continue
        return [seg]
    return []


def _segment_already_known(seg, categories):
    needle = f"/{seg}/"
    if seg in categories:
        return True
    for cat_id, meta in categories.items():
        if cat_id == "other":
            continue
        if cat_id == seg:
            return True
        for m in meta.get("match") or []:
            ml = (m or "").lower()
            if not ml:
                continue
            if ml == needle or ml.strip("/") == seg:
                return True
    return False


def discover_categories_from_hrefs(hrefs, categories, min_count=1):
    """
    Scan headline URLs that currently fall into 'other'. For each, take
    top path segments and, if unseen, add a new category entry.

    Mutates `categories` in place (keeps 'other' last).
    Returns a list of newly added category ids.
    """
    suggestions = Counter()
    for href in hrefs:
        if not href:
            continue
        cat_id, _ = match_category(href, categories)
        if cat_id != "other":
            continue
        for seg in _path_section_candidates(href):
            if _segment_already_known(seg, categories):
                continue
            suggestions[seg] += 1

    new_ids = []
    if not suggestions:
        return new_ids

    other_meta = categories.pop("other", dict(DEFAULT_CATEGORIES["other"]))
    used_colors = {m.get("color") for m in categories.values()}
    color_i = 0

    for seg, count in suggestions.most_common():
        if count < min_count:
            continue
        if _segment_already_known(seg, categories):
            continue
        cat_id = re.sub(r"[^a-z0-9_-]+", "-", seg).strip("-")
        if not cat_id or cat_id == "other" or cat_id in categories:
            continue

        color = AUTO_CATEGORY_COLORS[color_i % len(AUTO_CATEGORY_COLORS)]
        for _ in range(len(AUTO_CATEGORY_COLORS)):
            candidate = AUTO_CATEGORY_COLORS[color_i % len(AUTO_CATEGORY_COLORS)]
            color_i += 1
            if candidate not in used_colors:
                color = candidate
                break
        used_colors.add(color)

        label = seg.replace("-", " ").replace("_", " ").strip().title()
        categories[cat_id] = {
            "label": label,
            "color": color,
            "match": [f"/{seg}/"],
            "enabled": True,
        }
        new_ids.append(cat_id)

    categories["other"] = other_meta
    return new_ids


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


KNOWN_LANGUAGES = {
    "da": "Danish",
    "en": "English",
}


def normalize_languages(raw, available_codes=None):
    """
    Return an ordered dict-like mapping of language code -> enabled bool.
    Preserves known labels; unknown codes from available_codes are included.
    """
    available = list(available_codes or [])
    for code in KNOWN_LANGUAGES:
        if code not in available:
            available.append(code)
    # De-dupe while preserving order
    seen = set()
    codes = []
    for code in available:
        code = str(code or "").strip().lower()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)

    result = {}
    src = raw if isinstance(raw, dict) else {}
    for code in codes:
        if code in src:
            result[code] = bool(src[code])
        else:
            result[code] = True
    return result


def language_label(code):
    return KNOWN_LANGUAGES.get(code, code)


def site_language(entry, fallback="da"):
    if isinstance(entry, dict) and entry.get("language"):
        return str(entry.get("language")).strip().lower() or fallback
    return fallback


def normalize_site_order(order, site_ids):
    """Keep known ids in the given order; append any missing ids at the end."""
    known = list(site_ids)
    known_set = set(known)
    result = []
    seen = set()
    if isinstance(order, list):
        for sid in order:
            if not isinstance(sid, str):
                continue
            if sid in known_set and sid not in seen:
                result.append(sid)
                seen.add(sid)
    for sid in known:
        if sid not in seen:
            result.append(sid)
    return result


def load_sites_config(path, parsers):
    """
    Load/create sites.json. Keys are SITE_ID; each value has at least
    {"enabled": bool}. Unknown ids from old configs are kept but ignored
    at run time; newly discovered parsers are added as enabled=True.
    Legacy ``_settings`` keys are stripped (moved to settings.json).
    """
    config = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                config = data
            else:
                print(f"Warning: {path} isn't a JSON object, recreating.", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: couldn't read {path} ({e}), recreating.", file=sys.stderr)

    changed = False

    if "_settings" in config:
        del config["_settings"]
        changed = True

    for mod in parsers:
        lang = str(getattr(mod, "LANGUAGE", "da") or "da").strip().lower() or "da"
        entry = config.get(mod.SITE_ID)
        if not isinstance(entry, dict):
            config[mod.SITE_ID] = {
                "enabled": True,
                "name": mod.NAME,
                "url": mod.DEFAULT_URL,
                "language": lang,
            }
            changed = True
        else:
            # Keep enabled flag; refresh name/url/language metadata from the parser.
            if (
                entry.get("name") != mod.NAME
                or entry.get("url") != mod.DEFAULT_URL
                or entry.get("language") != lang
            ):
                entry["name"] = mod.NAME
                entry["url"] = mod.DEFAULT_URL
                entry["language"] = lang
                changed = True
            if "enabled" not in entry:
                entry["enabled"] = True
                changed = True

    if changed or not os.path.exists(path):
        save_sites_config(path, config)
        print(f"Updated {path} from discovered parsers.", file=sys.stderr)

    return config


def save_sites_config(path, config):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        print(f"Warning: couldn't write {path} ({e}).", file=sys.stderr)


def _read_legacy_settings_from_sites(sites_path):
    """Return sites.json ``_settings`` if present (pre-settings.json layouts)."""
    if not sites_path or not os.path.exists(sites_path):
        return None
    try:
        with open(sites_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("_settings"), dict):
            return data["_settings"]
    except (json.JSONDecodeError, OSError):
        pass
    return None


def load_settings_config(path, parsers, sites_path=None):
    """
    Load/create settings.json (global UI defaults). If missing, migrate from
    a legacy ``_settings`` block in sites.json when present.
    """
    settings = None
    changed = False

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                settings = data
            else:
                print(f"Warning: {path} isn't a JSON object, recreating.", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: couldn't read {path} ({e}), recreating.", file=sys.stderr)

    if settings is None:
        legacy = _read_legacy_settings_from_sites(sites_path)
        if isinstance(legacy, dict):
            settings = dict(legacy)
            print(f"Migrated settings from {sites_path} → {path}.", file=sys.stderr)
        else:
            settings = make_default_settings()
        changed = True

    defaults = make_default_settings()
    for key, value in defaults.items():
        if key not in settings:
            settings[key] = value
            changed = True
    if not isinstance(settings.get("site_order"), list):
        settings["site_order"] = []
        changed = True
    if not isinstance(settings.get("languages"), dict):
        settings["languages"] = dict(defaults["languages"])
        changed = True

    parser_ids = [mod.SITE_ID for mod in parsers]
    normalized_order = normalize_site_order(settings.get("site_order"), parser_ids)
    if settings.get("site_order") != normalized_order:
        settings["site_order"] = normalized_order
        changed = True

    available_langs = sorted({
        str(getattr(mod, "LANGUAGE", "da") or "da").strip().lower() or "da"
        for mod in parsers
    })
    normalized_langs = normalize_languages(settings.get("languages"), available_langs)
    if settings.get("languages") != normalized_langs:
        settings["languages"] = normalized_langs
        changed = True

    if changed or not os.path.exists(path):
        save_settings_config(path, settings)
        print(f"Updated {path}.", file=sys.stderr)

    return settings


def save_settings_config(path, config):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        print(f"Warning: couldn't write {path} ({e}).", file=sys.stderr)


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


def format_lines(items, with_links):
    out = []
    for text, href in items:
        if with_links and href:
            out.append(f"{text}\n  {href}")
        else:
            out.append(text)
    return out


def format_site_output(results, new_results, seen_results, is_first_run, no_cache_mode, with_links):
    if no_cache_mode or is_first_run:
        lines = format_lines(results, with_links)
        output_text = "\n".join(lines)
        if is_first_run:
            note = (
                f"({len(results)} headlines found - first run, nothing to compare against yet; "
                f"future runs will show a 'new' section)"
            )
        else:
            note = f"({len(results)} headlines found)"
    else:
        sections = []
        if new_results:
            sections.append(
                f"=== New since last run ({len(new_results)}) ===\n"
                + "\n".join(format_lines(new_results, with_links))
            )
        else:
            sections.append("=== New since last run (0) ===\nNone")
        if seen_results:
            sections.append(
                f"=== Previously seen ({len(seen_results)}) ===\n"
                + "\n".join(format_lines(seen_results, with_links))
            )
        output_text = "\n\n".join(sections)
        note = f"({len(results)} headlines found, {len(new_results)} new)"
    return output_text, note


def is_external_href(href, base_url, allowed_domains=None):
    """
    True if href points at a host outside this news site's domains.
    Relative URLs (no host) count as internal.
    """
    host = normalize_domain(urlparse(href or "").netloc)
    if not host:
        return False

    allowed = {normalize_domain(d) for d in (allowed_domains or ()) if d}
    if base_url:
        base_host = normalize_domain(urlparse(base_url).netloc)
        if base_host:
            allowed.add(base_host)

    for allowed_host in allowed:
        if not allowed_host:
            continue
        if (
            host == allowed_host
            or host.endswith("." + allowed_host)
            or allowed_host.endswith("." + host)
        ):
            return False
    return True


# Path segments that are structural, not article-title language.
_URL_TECH_SEGMENTS = {
    "www", "index", "html", "htm", "php", "asp", "aspx", "ece",
    "article", "articles", "story", "stories", "content", "node",
    "amp", "print", "preview", "live", "video", "reels", "rss", "feed",
    "tag", "tags", "category", "categories", "author", "authors",
}

_URL_ID_SEG = re.compile(r"^(art|article)?\d+$", re.I)
_URL_HAS_WORDS = re.compile(r"[-_]")


def url_language_tooltip(href):
    """
    If the URL path contains a near-natural-language article slug, return a
    human-readable version (tech stripped, -/_ -> spaces) for use as a tooltip.
    Returns None when the URL is not descriptive enough.
    """
    if not href:
        return None

    path = unquote(urlparse(href).path or "")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    # Drop trailing technical / id segments.
    while parts:
        last = parts[-1]
        low = last.lower()
        if (
            _URL_ID_SEG.match(low)
            or low in _URL_TECH_SEGMENTS
            or low.endswith((".html", ".htm", ".ece", ".php"))
            or (low.isdigit() and len(low) >= 4)
        ):
            parts.pop()
            continue
        # Strip extension from last segment if present.
        stem, _, ext = last.rpartition(".")
        if ext.lower() in ("html", "htm", "ece", "php", "aspx") and stem:
            parts[-1] = stem
        break

    if not parts:
        return None

    # Prefer the longest hyphenated/underscored segment (usually the title slug).
    candidates = []
    for seg in parts:
        low = seg.lower()
        if low in _URL_TECH_SEGMENTS or _URL_ID_SEG.match(low):
            continue
        if not _URL_HAS_WORDS.search(seg):
            continue
        words = [w for w in re.split(r"[-_]+", seg) if w]
        if len(words) < 2:
            continue
        # Need enough "language" signal: several words or a long phrase.
        if len(words) < 3 and len(seg) < 18:
            continue
        candidates.append(seg)

    if not candidates:
        return None

    slug = max(candidates, key=len)
    text = re.sub(r"[-_]+", " ", slug)
    text = re.sub(r"\s+", " ", text).strip()
    # Ignore if still looks technical / too short after cleanup.
    if len(text) < 12 or text.isdigit():
        return None
    return text


def _fold_for_tooltip_compare(text):
    """Normalize + fold so URL slugs compare fairly to headlines.

    Hyphens become spaces so '22-årig' and '22 årig' tokenize the same way
    before Danish letter folding.
    """
    t = (text or "").lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("-", " ").replace("_", " ")
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    for src, dst in (
        ("æ", "ae"),
        ("ø", "oe"),
        ("å", "aa"),
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
    ):
        t = t.replace(src, dst)
    return t


def _tooltip_word_count(text):
    folded = _fold_for_tooltip_compare(text)
    return len([w for w in folded.split() if w])


def tooltip_matches_headline(tip, headline, threshold=0.72):
    """
    True when the URL-derived tooltip is close enough to the headline that
    showing it adds no useful information.

    If the URL wording has more words than the headline (after hyphen/space
    normalization), keep the tip — it may add detail the headline omitted.
    """
    if not tip or not headline:
        return False
    a = _fold_for_tooltip_compare(tip)
    b = _fold_for_tooltip_compare(headline)
    if not a or not b:
        return False
    tip_n = len([w for w in a.split() if w])
    head_n = len([w for w in b.split() if w])
    if tip_n > head_n:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 12 and shorter in longer:
        return True
    if SequenceMatcher(None, a, b).ratio() >= threshold:
        return True
    # Word overlap: most slug words already appear in the headline.
    tip_words = [w for w in a.split() if len(w) >= 3]
    if len(tip_words) >= 3:
        head_words = set(b.split())
        hits = sum(1 for w in tip_words if w in head_words)
        if hits / len(tip_words) >= 0.7:
            return True
    return False


def parse_highlight_words(raw):
    """Split a comma-separated highlight string into cleaned lowercase keywords."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw).split(",")
    words = []
    seen = set()
    for part in parts:
        w = re.sub(r"\s+", " ", str(part)).strip().casefold()
        if len(w) < 2 or w in seen:
            continue
        seen.add(w)
        words.append(w)
    return words


def headline_matches_keywords(text, keywords):
    if not keywords or not text:
        return False
    hay = text.casefold()
    return any(k in hay for k in keywords)


def _render_headline_li(
    text,
    href,
    categories,
    base_url,
    allowed_domains,
    show_external=True,
    is_new=True,
    highlight_words=None,
    exclude_words=None,
    seen_index=None,
    site_id=None,
    language=None,
    source_badge_html="",
):
    keywords = highlight_words if highlight_words is not None else []
    exclude = exclude_words if exclude_words is not None else []
    cat_id, meta = match_category(href, categories)
    color = meta.get("color")
    cat_enabled = bool(meta.get("enabled", True))
    external = is_external_href(href, base_url, allowed_domains)
    style = f' style="--hl-bg: {html.escape(color)};"' if color else ""
    classes = ["headline"]
    if not cat_enabled:
        classes.append("hidden-cat")
    if external and not show_external:
        classes.append("hidden-external")
    if headline_matches_keywords(text, exclude):
        classes.append("hidden-exclude")
    if headline_matches_keywords(text, keywords):
        classes.append("hl-keyword")
    ext_attr = "1" if external else "0"
    new_attr = "1" if is_new else "0"
    badge = ' <span class="ext-badge" title="Off-site link">↗</span>' if external else ""
    tip = url_language_tooltip(href)
    if tip and tooltip_matches_headline(tip, text):
        tip = None
    title_attr = f' title="{html.escape(tip, quote=True)}"' if tip else ""
    tip_html = (
        f'<span class="url-tip"{title_attr}>{html.escape(tip)}</span>'
        if tip
        else ""
    )
    index_attr = "" if seen_index is None else f' data-seen-index="{seen_index}"'
    site_attr = f' data-site="{html.escape(site_id)}"' if site_id else ""
    lang_attr = f' data-lang="{html.escape(language)}"' if language else ""
    href_attr = f' data-href="{html.escape(href, quote=True)}"'
    return (
        f'        <li class="{" ".join(classes)}" data-category="{html.escape(cat_id)}" '
        f'data-external="{ext_attr}" data-new="{new_attr}"{site_attr}{lang_attr}{href_attr}{index_attr}{style}>'
        f'{source_badge_html}'
        f'<span class="hl-main"><a href="{html.escape(href)}" target="_blank" '
        f'rel="noopener"{title_attr}>{html.escape(text)}</a>{badge}</span>'
        f'{tip_html}</li>'
    )


def _render_item_list(
    results,
    categories,
    base_url,
    allowed_domains,
    show_external=True,
    is_new=True,
    highlight_words=None,
    exclude_words=None,
    site_id=None,
    language=None,
):
    rows = []
    for idx, (text, href) in enumerate(results):
        rows.append(
            _render_headline_li(
                text,
                href,
                categories,
                base_url,
                allowed_domains,
                show_external=show_external,
                is_new=is_new,
                highlight_words=highlight_words,
                exclude_words=exclude_words,
                seen_index=None if is_new else idx,
                site_id=site_id,
                language=language,
            )
        )
    return "\n".join(rows) if rows else '        <li class="empty">No headlines</li>'


def _source_badge_html(block):
    name = html.escape(block["name"])
    logo_path = block.get("logo")
    if logo_path:
        return (
            f'<span class="src-badge" title="{name}">'
            f'<img src="{html.escape(logo_path, quote=True)}" alt="" width="14" height="14">'
            f'<span class="src-name">{name}</span></span>'
        )
    return f'<span class="src-badge" title="{name}"><span class="src-name">{name}</span></span>'


def _render_all_new_section(
    site_blocks,
    categories,
    site_domains,
    show_external,
    highlight_keywords,
    show_all_new,
    languages=None,
    exclude_keywords=None,
):
    langs = languages if isinstance(languages, dict) else {}
    exclude = exclude_keywords if exclude_keywords is not None else []
    rows = []
    for block in site_blocks:
        if block.get("error"):
            continue
        sid = block["site_id"]
        lang = block.get("language") or "da"
        if block.get("is_first_run"):
            items = block.get("seen_results") or []
        else:
            items = block.get("new_results") or []
        if not items:
            continue
        allowed = site_domains.get(sid) or ()
        badge = _source_badge_html(block)
        lang_hidden = langs.get(lang, True) is False
        for text, href in items:
            li = _render_headline_li(
                text,
                href,
                categories,
                block["url"],
                allowed,
                show_external=show_external,
                is_new=True,
                highlight_words=highlight_keywords,
                exclude_words=exclude,
                site_id=sid,
                language=lang,
                source_badge_html=badge,
            )
            if lang_hidden:
                li = li.replace('class="headline', 'class="headline hidden-lang', 1)
            rows.append(li)

    hidden_class = "" if show_all_new else " hidden"
    if not rows:
        body = '      <p class="empty">No new headlines across enabled sites.</p>'
        count = 0
    else:
        body = (
            f'      <ol class="all-new-list">\n'
            + "\n".join(rows)
            + "\n      </ol>"
        )
        count = len(rows)

    return f"""  <section class="all-new{hidden_class}" id="all-new" data-panel="all-new" data-collapse-id="all-new">
    <h2 class="site-title">
      <button type="button" class="site-collapse-toggle" aria-expanded="true" aria-controls="all-new-body" title="Collapse or expand">
        <span class="site-name">All new</span>
      </button>
      <span class="count-inline" id="all-new-count">({count})</span>
    </h2>
    <div class="site-body" id="all-new-body">
{body}
    </div>
  </section>"""


def build_combined_html(site_blocks, categories, sites_config, settings, site_domains):
    """
    site_blocks: list of dicts with keys:
      site_id, name, url, new_results, seen_results, is_first_run, error (optional)
    site_domains: site_id -> iterable of allowed hostnames
    """
    if not isinstance(settings, dict):
        settings = {}
    show_external = bool(settings.get("show_external", True))
    only_new = bool(settings.get("only_new", False))
    show_all_new = bool(settings.get("show_all_new", True))
    dim_opened = bool(settings.get("dim_opened", True))
    show_opened_today = bool(settings.get("show_opened_today", True))
    dark_mode = bool(settings.get("dark_mode", False))
    available_langs = sorted({
        site_language(sites_config.get(b["site_id"]), b.get("language") or "da")
        for b in site_blocks
    } | set(KNOWN_LANGUAGES))
    languages = normalize_languages(settings.get("languages"), available_langs)
    try:
        bg_strength = int(settings.get("bg_strength", 35))
    except (TypeError, ValueError):
        bg_strength = 35
    bg_strength = max(0, min(100, bg_strength))
    try:
        seen_limit = int(settings.get("seen_limit", 15))
    except (TypeError, ValueError):
        seen_limit = 15
    seen_limit = max(0, min(500, seen_limit))
    highlight_words_raw = settings.get("highlight_words", "")
    if isinstance(highlight_words_raw, (list, tuple)):
        highlight_words_raw = ", ".join(str(w) for w in highlight_words_raw)
    else:
        highlight_words_raw = str(highlight_words_raw or "")
    highlight_keywords = parse_highlight_words(highlight_words_raw)
    exclude_words_raw = settings.get("exclude_words", "")
    if isinstance(exclude_words_raw, (list, tuple)):
        exclude_words_raw = ", ".join(str(w) for w in exclude_words_raw)
    else:
        exclude_words_raw = str(exclude_words_raw or "")
    exclude_keywords = parse_highlight_words(exclude_words_raw)
    site_order = normalize_site_order(
        settings.get("site_order"),
        [block["site_id"] for block in site_blocks],
    )
    order_index = {sid: i for i, sid in enumerate(site_order)}
    site_blocks = sorted(
        site_blocks,
        key=lambda b: (order_index.get(b["site_id"], 10_000), b["site_id"]),
    )

    site_toggle_rows = []
    sections = []
    initial_sites = {}
    site_lang_map = {}

    for block in site_blocks:
        sid = block["site_id"]
        entry = sites_config.get(sid, {})
        lang = site_language(entry, block.get("language") or "da")
        site_lang_map[sid] = lang
        lang_on = languages.get(lang, True)
        enabled = bool(entry.get("enabled", True))
        initial_sites[sid] = enabled
        checked = " checked" if enabled else ""
        hidden_class = "" if (enabled and lang_on) else " hidden"
        allowed = site_domains.get(sid) or ()
        logo_path = block.get("logo")
        if logo_path:
            toggle_logo = (
                f'<img class="site-logo-sm" src="{html.escape(logo_path, quote=True)}" '
                f'alt="" width="16" height="16">'
            )
        else:
            toggle_logo = ""
        site_toggle_rows.append(
            f'      <label class="toggle site-toggle" data-site="{html.escape(sid)}" '
            f'data-lang="{html.escape(lang)}" draggable="true">'
            f'<span class="drag-handle" title="Drag to reorder" aria-hidden="true">⋮⋮</span>'
            f'<input type="checkbox" data-site="{html.escape(sid)}"{checked}> '
            f'{toggle_logo}{html.escape(block["name"])}'
            f'<span class="lang-tag">{html.escape(language_label(lang))}</span></label>'
        )

        if block.get("error"):
            body = f'    <p class="error">{html.escape(block["error"])}</p>'
        elif block["is_first_run"]:
            # First run has no baseline; treat all as new so "only new" still shows them.
            body = f"""    <div class="freshness-block" data-freshness="new">
    <div class="count">{len(block["seen_results"])} headlines (first run)</div>
    <ol>
{_render_item_list(block["seen_results"], categories, block["url"], allowed, show_external, is_new=True, highlight_words=highlight_keywords, exclude_words=exclude_keywords, site_id=sid, language=lang)}
    </ol>
    </div>"""
        else:
            new_html = (
                f"""    <div class="freshness-block" data-freshness="new">
    <h3>New since last run ({len(block["new_results"])})</h3>
    <ol class="new">
{_render_item_list(block["new_results"], categories, block["url"], allowed, show_external, is_new=True, highlight_words=highlight_keywords, exclude_words=exclude_keywords, site_id=sid, language=lang)}
    </ol>
    </div>"""
                if block["new_results"]
                else """    <div class="freshness-block" data-freshness="new">
    <h3>New since last run (0)</h3>
    <p class="empty">No new headlines since last run.</p>
    </div>"""
            )
            seen_total = len(block["seen_results"])
            seen_html = (
                f"""    <div class="freshness-block" data-freshness="seen" data-seen-total="{seen_total}" data-seen-page="0">
    <h3 class="seen-heading">Previously seen (<span class="seen-shown">{min(seen_limit, seen_total)}</span> of <span class="seen-total">{seen_total}</span>)</h3>
    <ol>
{_render_item_list(block["seen_results"], categories, block["url"], allowed, show_external, is_new=False, highlight_words=highlight_keywords, exclude_words=exclude_keywords, site_id=sid, language=lang)}
    </ol>
    <nav class="seen-pager" aria-label="Previously seen pages">
      <button type="button" class="seen-prev">Previous {seen_limit}</button>
      <span class="seen-page-label">(1 of 1)</span>
      <button type="button" class="seen-next">Next {seen_limit}</button>
    </nav>
    </div>"""
                if block["seen_results"]
                else ""
            )
            body = new_html + "\n" + seen_html

        if logo_path:
            logo_html = (
                f'<img class="site-logo" src="{html.escape(logo_path, quote=True)}" '
                f'alt="" width="40" height="40" loading="lazy">'
            )
        else:
            logo_html = ""

        if block.get("error"):
            new_count = 0
        elif block["is_first_run"]:
            new_items = block["seen_results"]
            new_count = sum(
                1 for text, _href in new_items
                if not headline_matches_keywords(text, exclude_keywords)
            )
        else:
            new_items = block["new_results"]
            new_count = sum(
                1 for text, _href in new_items
                if not headline_matches_keywords(text, exclude_keywords)
            )
        if new_count == 1:
            new_count_html = '<span class="collapsed-new-count">1 new article</span>'
        elif new_count > 1:
            new_count_html = (
                f'<span class="collapsed-new-count">{new_count} new articles</span>'
            )
        else:
            new_count_html = ""

        body_id = f"site-body-{sid}"
        sections.append(
            f"""  <section class="site{hidden_class}" id="site-{html.escape(sid)}" """
            f"""data-site="{html.escape(sid)}" data-lang="{html.escape(lang)}">
    <h2 class="site-title">
      <button type="button" class="site-collapse-toggle" aria-expanded="true" """
            f"""aria-controls="{html.escape(body_id)}" title="Collapse or expand">
        {logo_html}<span class="site-name">{html.escape(block["name"])}</span>
      </button>
      {new_count_html}
    </h2>
    <div class="site-body" id="{html.escape(body_id)}">
{body}
    </div>
  </section>"""
        )

    all_new_section = _render_all_new_section(
        site_blocks,
        categories,
        site_domains,
        show_external,
        highlight_keywords,
        show_all_new,
        languages=languages,
        exclude_keywords=exclude_keywords,
    )
    opened_hidden = "" if show_opened_today else " hidden"
    opened_today_section = f"""  <section class="opened-today{opened_hidden}" id="opened-today" data-panel="opened-today">
    <h2 class="site-title">Opened today <span class="count-inline" id="opened-today-count">(0)</span></h2>
    <ol id="opened-today-list">
      <li class="empty">No articles opened today.</li>
    </ol>
  </section>"""

    cat_toggle_rows = []
    initial_cats = {}
    sorted_cats = sorted(
        categories.items(),
        key=lambda item: (str(item[1].get("label") or item[0]).casefold(), item[0]),
    )
    for cat_id, meta in sorted_cats:
        initial_cats[cat_id] = bool(meta.get("enabled", True))
        checked = " checked" if initial_cats[cat_id] else ""
        color = html.escape(meta.get("color") or "#eee")
        label = html.escape(meta.get("label") or cat_id)
        cat_toggle_rows.append(
            f'      <label class="toggle cat-toggle" data-category="{html.escape(cat_id)}">'
            f'<input type="checkbox" data-category="{html.escape(cat_id)}"{checked}> '
            f'<span class="swatch" style="background:{color}"></span>{label}</label>'
        )

    ext_checked = " checked" if show_external else ""
    only_new_checked = " checked" if only_new else ""
    all_new_checked = " checked" if show_all_new else ""
    dim_opened_checked = " checked" if dim_opened else ""
    opened_today_checked = " checked" if show_opened_today else ""
    dark_mode_checked = " checked" if dark_mode else ""
    body_classes = []
    if dim_opened:
        body_classes.append("dim-opened")
    if dark_mode:
        body_classes.append("dark")
    body_class_attr = f' class="{" ".join(body_classes)}"' if body_classes else ""
    sites_json_literal = json.dumps(sites_config, ensure_ascii=False)
    settings_json_literal = json.dumps(settings, ensure_ascii=False)
    categories_json_literal = json.dumps(categories, ensure_ascii=False)
    initial_sites_literal = json.dumps(initial_sites)
    initial_cats_literal = json.dumps(initial_cats)
    initial_external_literal = json.dumps(show_external)
    initial_only_new_literal = json.dumps(only_new)
    initial_all_new_literal = json.dumps(show_all_new)
    initial_dim_opened_literal = json.dumps(dim_opened)
    initial_opened_today_literal = json.dumps(show_opened_today)
    initial_dark_mode_literal = json.dumps(dark_mode)
    initial_bg_strength_literal = json.dumps(bg_strength)
    initial_seen_limit_literal = json.dumps(seen_limit)
    initial_highlight_words_literal = json.dumps(highlight_words_raw, ensure_ascii=False)
    initial_exclude_words_literal = json.dumps(exclude_words_raw, ensure_ascii=False)
    initial_site_order_literal = json.dumps(site_order)
    initial_languages_literal = json.dumps(languages)
    site_lang_map_literal = json.dumps(site_lang_map)
    highlight_words_attr = html.escape(highlight_words_raw, quote=True)
    exclude_words_attr = html.escape(exclude_words_raw, quote=True)

    lang_toggle_rows = []
    for code, enabled in languages.items():
        checked = " checked" if enabled else ""
        lang_toggle_rows.append(
            f'      <label class="toggle">'
            f'<input type="checkbox" data-lang="{html.escape(code)}"{checked}> '
            f'{html.escape(language_label(code))}</label>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NewsExtract</title>
<style>
  :root {{
    --bg: #f4f5f7;
    --panel: #ffffff;
    --text: #1a1a1a;
    --muted: #666;
    --border: #ddd;
    --accent: #1565c0;
    --link: #2c2c2c;
    --link-opened: #6e6e6e;
    --hover: #f0f0f0;
    --hover-soft: #f3f5f7;
    --empty: #999;
    --error: #b71c1c;
    --tip: #8a8f98;
    --subhead: #333;
    --btn-face: #fafafa;
    --hl-bg-strength: {bg_strength}%;
    --font-body: "Lato", Helvetica, Arial, sans-serif;
  }}
  body.dark {{
    --bg: #12151a;
    --panel: #1c2128;
    --text: #e8eaed;
    --muted: #9aa0a8;
    --border: #2f3640;
    --accent: #5b9fd4;
    --link: #d0d0d0;
    --link-opened: #8a8a8a;
    --hover: #2a313a;
    --hover-soft: #252b34;
    --empty: #7a828c;
    --error: #f28b82;
    --tip: #8b939e;
    --subhead: #c5cad1;
    --btn-face: #252b34;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-Regular.ttf") format("truetype");
    font-weight: 400;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-Italic.ttf") format("truetype");
    font-weight: 400;
    font-style: italic;
    font-display: swap;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-Bold.ttf") format("truetype");
    font-weight: 700;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-BoldItalic.ttf") format("truetype");
    font-weight: 700;
    font-style: italic;
    font-display: swap;
  }}
  body {{
    font-family: var(--font-body);
    max-width: 840px;
    margin: 0 auto;
    padding: 24px 20px 60px;
    color: var(--text);
    background: var(--bg);
  }}
  .page-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.2em;
  }}
  .page-header .brand {{
    display: flex;
    align-items: center;
    gap: 0.65em;
    min-width: 0;
  }}
  .page-header .brand-logo {{
    width: 48px;
    height: 48px;
    object-fit: contain;
    flex: 0 0 auto;
    border-radius: 8px;
  }}
  .page-header h1 {{
    font-family: var(--font-body);
    font-size: 1.75rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: 0.02em;
  }}
  .header-actions {{
    display: flex;
    align-items: center;
    gap: 0.5em;
    flex: 0 0 auto;
  }}
  .btn-settings,
  .btn-theme {{
    font: inherit;
    font-size: 0.9rem;
    padding: 0.4em 0.85em;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel);
    color: var(--text);
    cursor: pointer;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }}
  .btn-settings:hover,
  .btn-theme:hover {{ background: var(--hover); }}
  .settings-backdrop {{
    display: none;
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(20, 24, 28, 0.45);
    align-items: flex-start;
    justify-content: center;
    padding: 8vh 16px 24px;
    overflow: auto;
  }}
  .settings-backdrop.open {{ display: flex; }}
  .settings-panel {{
    width: min(560px, 100%);
    max-height: min(80vh, 720px);
    overflow: auto;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 18px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.18);
  }}
  .settings-panel-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.6em;
  }}
  .settings-panel-header h2 {{
    font-family: var(--font-body);
    font-size: 1.25rem;
    font-weight: 700;
    margin: 0;
    color: var(--text);
    text-transform: none;
    letter-spacing: 0.02em;
  }}
  .btn-close {{
    font: inherit;
    font-size: 1.2rem;
    line-height: 1;
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--btn-face);
    cursor: pointer;
    color: var(--muted);
  }}
  .btn-close:hover {{ background: var(--hover); color: var(--text); }}
  .settings-section-title {{
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin: 1em 0 0.5em;
  }}
  .settings-section-title:first-of-type {{ margin-top: 0.2em; }}
  .toggles {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.45em 0.9em;
    margin-bottom: 0.5em;
  }}
  #site-toggles {{
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: 0.25em;
  }}
  #site-toggles .site-toggle {{
    display: flex;
    align-items: center;
    gap: 0.4em;
    padding: 0.3em 0.4em;
    border: 1px solid transparent;
    border-radius: 6px;
    cursor: grab;
  }}
  #site-toggles .site-toggle:hover {{
    background: var(--hover-soft);
  }}
  #site-toggles .site-toggle.dragging {{
    opacity: 0.45;
  }}
  #site-toggles .site-toggle.drag-over {{
    border-top-color: var(--accent);
  }}
  #site-toggles .drag-handle {{
    color: var(--muted);
    font-size: 0.85rem;
    letter-spacing: -0.12em;
    line-height: 1;
    padding: 0 0.15em;
    cursor: grab;
    flex: 0 0 auto;
  }}
  #site-toggles .site-logo-sm {{
    width: 16px;
    height: 16px;
    object-fit: contain;
    flex: 0 0 auto;
    border-radius: 2px;
  }}
  #site-toggles .lang-tag {{
    margin-left: auto;
    font-size: 0.72rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  #site-toggles .site-toggle.hidden-lang {{
    display: none;
  }}
  .sites-hint {{
    font-size: 0.8rem;
    color: var(--muted);
    margin: 0 0 0.55em;
  }}
  .toggle {{
    display: inline-flex;
    align-items: center;
    gap: 0.35em;
    font-size: 0.92rem;
    cursor: pointer;
    user-select: none;
  }}
  .swatch {{
    width: 0.85em;
    height: 0.85em;
    border-radius: 3px;
    border: 1px solid rgba(0,0,0,0.15);
    flex-shrink: 0;
  }}
  .toolbar-actions {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5em;
    align-items: center;
    margin-top: 0.35em;
  }}
  button {{
    font: inherit;
    font-size: 0.9rem;
    padding: 0.35em 0.75em;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--btn-face);
    color: var(--text);
    cursor: pointer;
  }}
  button:hover {{ background: var(--hover); }}
  button.primary {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }}
  button.primary:hover {{ filter: brightness(1.05); }}
  .hint {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.7em; }}
  .status {{ font-size: 0.85rem; color: var(--accent); min-height: 1.2em; }}
  .slider-row {{
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.35em 0.75em;
    align-items: center;
    margin: 0.35em 0 0.6em;
  }}
  .slider-row label {{
    grid-column: 1 / -1;
    font-size: 0.92rem;
  }}
  .slider-row input[type="range"],
  .slider-row input[type="number"] {{
    width: 100%;
    accent-color: var(--accent);
  }}
  .slider-row input[type="number"] {{
    font: inherit;
    padding: 0.25em 0.4em;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--panel);
    color: var(--text);
  }}
  .slider-row .slider-value {{
    font-size: 0.85rem;
    color: var(--muted);
    min-width: 2.5em;
    text-align: right;
  }}
  .slider-row input[type="text"] {{
    grid-column: 1 / -1;
    font: inherit;
    padding: 0.35em 0.5em;
    border: 1px solid var(--border);
    border-radius: 4px;
    width: 100%;
    box-sizing: border-box;
    background: var(--panel);
    color: var(--text);
  }}
  body.settings-open {{ overflow: hidden; }}
  section.site {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1em 1.2em 1.2em;
    margin-bottom: 1.2em;
  }}
  section.site.hidden {{ display: none; }}
  section.site.collapsed {{
    padding-bottom: 1em;
  }}
  section.site.collapsed > .site-body {{ display: none; }}
  section.site.collapsed > .site-title {{ margin-bottom: 0; }}
  section.all-new,
  section.opened-today {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1em 1.2em 1.2em;
    margin-bottom: 1.2em;
  }}
  section.all-new.hidden,
  section.opened-today.hidden {{ display: none; }}
  section.all-new.collapsed {{
    padding-bottom: 1em;
  }}
  section.all-new.collapsed > .site-body {{ display: none; }}
  section.all-new.collapsed > .site-title {{ margin-bottom: 0; }}
  .count-inline {{
    color: var(--muted);
    font-weight: 500;
    font-size: 0.9em;
  }}
  .src-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    flex: 0 0 auto;
    max-width: 7.5em;
    margin-right: 0.15em;
    color: var(--muted);
    font-size: 0.75em;
  }}
  .src-badge img {{
    width: 14px;
    height: 14px;
    object-fit: contain;
    border-radius: 2px;
  }}
  .src-badge .src-name {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  body.dim-opened li.headline.opened {{
    opacity: 0.42;
  }}
  body.dim-opened li.headline.opened .hl-main a {{
    color: var(--link-opened);
  }}
  li.headline.hidden-cat,
  li.headline.hidden-external,
  li.headline.hidden-site,
  li.headline.hidden-lang,
  li.headline.hidden-exclude {{ display: none; }}
  body.only-new .freshness-block[data-freshness="seen"] {{ display: none; }}
  body.only-new li.headline[data-new="0"] {{ display: none; }}
  li.headline.hidden-seen-limit {{ display: none; }}
  .freshness-block.hidden-seen-limit-block {{ display: none; }}
  .seen-pager {{
    display: none;
    align-items: center;
    justify-content: flex-end;
    gap: 0.6em;
    margin-top: 0.75em;
    flex-wrap: wrap;
    font-size: 0.72rem;
  }}
  .freshness-block[data-freshness="seen"].has-seen-pages > .seen-pager {{
    display: flex;
  }}
  .seen-pager .seen-page-label {{
    font-size: inherit;
    font-weight: 500;
    color: var(--muted);
    min-width: 4em;
    text-align: center;
  }}
  .seen-pager button {{
    font-size: inherit;
    padding: 0.2em 0.5em;
  }}
  .seen-pager button:disabled {{
    opacity: 0.45;
    cursor: default;
  }}
  .seen-pager button:disabled:hover {{
    background: var(--btn-face);
  }}
  li.seen-pad {{
    visibility: hidden;
    pointer-events: none;
    user-select: none;
    display: flex;
    align-items: baseline;
    gap: 0.55em;
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    margin: 0.35em 0;
    padding: 0.15em 0.4em;
    line-height: 1.4;
    border-radius: 4px;
  }}
  .ext-badge {{
    color: var(--muted);
    font-size: 0.75em;
    margin-left: 0.25em;
  }}
  h2.site-title {{
    display: flex;
    align-items: center;
    gap: 0.45em;
    font-family: var(--font-body);
    font-size: 1.35rem;
    font-weight: 700;
    margin: 0 0 0.6em;
  }}
  h2.site-title .site-collapse-toggle {{
    display: inline-flex;
    align-items: center;
    gap: 0.45em;
    margin: 0;
    padding: 0;
    border: none;
    background: transparent;
    color: inherit;
    font: inherit;
    font-weight: inherit;
    cursor: pointer;
    text-align: left;
  }}
  h2.site-title .site-collapse-toggle:focus-visible {{
    outline: 2px solid var(--link);
    outline-offset: 3px;
    border-radius: 4px;
  }}
  h2.site-title .collapsed-new-count {{
    display: none;
    margin-left: auto;
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--muted);
    white-space: nowrap;
  }}
  section.site.collapsed > .site-title .collapsed-new-count {{
    display: inline;
  }}
  h2.site-title .site-logo {{
    width: 2.5rem;
    height: 2.5rem;
    max-width: 48px;
    max-height: 48px;
    object-fit: contain;
    flex: 0 0 auto;
    border-radius: 3px;
  }}
  h2 {{
    font-family: var(--font-body);
    font-size: 1.25rem;
    font-weight: 700;
    margin: 0 0 0.6em;
  }}
  h2 a {{ color: inherit; text-decoration: none; }}
  h2 a:hover {{ text-decoration: underline; }}
  h3 {{
    font-family: var(--font-body);
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--subhead);
    margin: 1em 0 0.4em;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.25em;
  }}
  ol {{ padding-left: 1.4em; margin: 0.3em 0; }}
  li {{ margin: 0.35em 0; line-height: 1.4; padding: 0.15em 0.4em; border-radius: 4px; }}
  li.headline {{
    display: flex;
    align-items: baseline;
    gap: 0.55em;
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    background-color: color-mix(in srgb, var(--hl-bg, transparent) var(--hl-bg-strength), transparent);
  }}
  li.headline > .hl-main {{
    display: flex;
    align-items: baseline;
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
  }}
  li.headline:has(> .url-tip) > .hl-main {{
    flex: 0 1 auto;
    max-width: 70%;
  }}
  li.headline > .hl-main > a {{
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  li.headline.hl-keyword > .hl-main > a {{
    font-weight: 700;
  }}
  li.headline > .hl-main > .ext-badge {{
    flex: 0 0 auto;
  }}
  li.headline > .url-tip {{
    flex: 1 1 0;
    min-width: 3em;
    color: var(--tip);
    font-size: 0.82em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .count {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 0.6em; }}
  .empty {{ color: var(--empty); font-style: italic; }}
  .error {{ color: var(--error); }}
  footer.page-footer {{
    margin: 2.5em 0 1.5em;
    text-align: center;
    font-size: 0.7rem;
    line-height: 1.4;
    color: var(--text);
    opacity: 0.3;
  }}
</style>
</head>
<body{body_class_attr}>
  <div class="page-header">
    <div class="brand">
      <img class="brand-logo" src="logos/newsextract_256.png" alt="" width="48" height="48">
      <h1>NewsExtract</h1>
    </div>
    <div class="header-actions">
      <button type="button" class="btn-theme" id="btn-theme" aria-pressed="{str(dark_mode).lower()}" title="Toggle dark / light mode">{("Light" if dark_mode else "Dark")}</button>
      <button type="button" class="btn-settings" id="btn-open-settings" aria-haspopup="dialog">Settings</button>
    </div>
  </div>

  <div class="settings-backdrop" id="settings-backdrop" role="presentation">
    <div class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabindex="-1">
      <div class="settings-panel-header">
        <h2 id="settings-title">Settings</h2>
        <button type="button" class="btn-close" id="btn-close-settings" aria-label="Close settings">&times;</button>
      </div>

      <div class="settings-section-title">Languages</div>
      <p class="sites-hint">Turn languages off to hide those news sources.</p>
      <div class="toggles" id="lang-toggles">
{chr(10).join(lang_toggle_rows)}
      </div>

      <div class="settings-section-title">Sites</div>
      <p class="sites-hint">Drag to reorder how sites appear on the page.</p>
      <div class="toggles" id="site-toggles">
{chr(10).join(site_toggle_rows)}
      </div>
      <div class="toolbar-actions">
        <button type="button" id="btn-sites-all">All on</button>
        <button type="button" id="btn-sites-none">All off</button>
      </div>

      <div class="settings-section-title">Categories</div>
      <div class="toggles" id="cat-toggles">
{chr(10).join(cat_toggle_rows)}
      </div>
      <div class="toolbar-actions">
        <button type="button" id="btn-cats-all">All on</button>
        <button type="button" id="btn-cats-none">All off</button>
      </div>

      <div class="settings-section-title">Filters</div>
      <div class="toggles" id="link-toggles">
        <label class="toggle">
          <input type="checkbox" id="toggle-external"{ext_checked}>
          Off-site links <span class="ext-badge">↗</span>
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-only-new"{only_new_checked}>
          Only new articles
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-all-new"{all_new_checked}>
          Show “All new” feed
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-dim-opened"{dim_opened_checked}>
          Dim opened links
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-opened-today"{opened_today_checked}>
          Show “Opened today”
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-dark"{dark_mode_checked}>
          Dark mode
        </label>
      </div>
      <div class="slider-row">
        <label for="slider-bg-strength">Category color strength</label>
        <input type="range" id="slider-bg-strength" min="0" max="100" step="1" value="{bg_strength}">
        <span class="slider-value" id="slider-bg-strength-value">{bg_strength}%</span>
      </div>
      <div class="slider-row">
        <label for="input-seen-limit">Previously seen headlines shown (per site)</label>
        <input type="number" id="input-seen-limit" min="0" max="500" step="1" value="{seen_limit}">
        <span class="slider-value" id="input-seen-limit-value">{seen_limit}</span>
      </div>
      <div class="slider-row">
        <label for="input-highlight-words">Bold headlines containing (comma-separated)</label>
        <input type="text" id="input-highlight-words" value="{highlight_words_attr}"
          placeholder="e.g. trump, grønland, ukraine" autocomplete="off" spellcheck="false">
      </div>
      <div class="slider-row">
        <label for="input-exclude-words">Exclude headlines containing (comma-separated)</label>
        <input type="text" id="input-exclude-words" value="{exclude_words_attr}"
          placeholder="e.g. reality, influencers, royal" autocomplete="off" spellcheck="false">
      </div>

      <div class="toolbar-actions" style="margin-top:1em">
        <button type="button" class="primary" id="btn-export">Save for next run</button>
        <button type="button" id="btn-clear-storage">Clear all cookies &amp; saved settings</button>
        <span class="status" id="status"></span>
      </div>
      <p class="hint">Downloads updated <code>sites.json</code>, <code>settings.json</code>, and <code>categories.json</code>.
        Replace those files in the NewsExtract folder to apply on the next script run.
        “Clear all…” removes NewsExtract data stored in this browser (localStorage / cookies).</p>
    </div>
  </div>

{all_new_section}
{opened_today_section}
{chr(10).join(sections)}

<script>
(function () {{
  const SITE_KEY = "newsextract.siteVisibility";
  const CAT_KEY = "newsextract.categoryVisibility";
  const EXT_KEY = "newsextract.showExternal";
  const ONLY_NEW_KEY = "newsextract.onlyNew";
  const ALL_NEW_KEY = "newsextract.showAllNew";
  const DIM_OPENED_KEY = "newsextract.dimOpened";
  const OPENED_TODAY_KEY = "newsextract.showOpenedToday";
  const DARK_MODE_KEY = "newsextract.darkMode";
  const LANG_KEY = "newsextract.languages";
  const OPENED_LINKS_KEY = "newsextract.openedLinks";
  const COLLAPSED_KEY = "newsextract.collapsedSites";
  const sitesConfig = {sites_json_literal};
  const settingsConfig = {settings_json_literal};
  const categoriesConfig = {categories_json_literal};
  const initialSites = {initial_sites_literal};
  const initialCats = {initial_cats_literal};
  const initialExternal = {initial_external_literal};
  const initialOnlyNew = {initial_only_new_literal};
  const initialAllNew = {initial_all_new_literal};
  const initialDimOpened = {initial_dim_opened_literal};
  const initialOpenedToday = {initial_opened_today_literal};
  const initialDarkMode = {initial_dark_mode_literal};
  const initialBgStrength = {initial_bg_strength_literal};
  const initialSeenLimit = {initial_seen_limit_literal};
  const initialHighlightWords = {initial_highlight_words_literal};
  const initialExcludeWords = {initial_exclude_words_literal};
  const initialSiteOrder = {initial_site_order_literal};
  const initialLanguages = {initial_languages_literal};
  const siteLangMap = {site_lang_map_literal};

  const backdrop = document.getElementById("settings-backdrop");
  const openBtn = document.getElementById("btn-open-settings");
  const closeBtn = document.getElementById("btn-close-settings");

  function openSettings() {{
    backdrop.classList.add("open");
    document.body.classList.add("settings-open");
    closeBtn.focus();
  }}

  function closeSettings() {{
    backdrop.classList.remove("open");
    document.body.classList.remove("settings-open");
    openBtn.focus();
  }}

  openBtn.addEventListener("click", openSettings);
  closeBtn.addEventListener("click", closeSettings);
  backdrop.addEventListener("click", function (e) {{
    if (e.target === backdrop) closeSettings();
  }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape" && backdrop.classList.contains("open")) closeSettings();
  }});

    function loadState(key, fallback) {{
    try {{
      const raw = localStorage.getItem(key);
      if (!raw) return Object.assign({{}}, fallback);
      return Object.assign({{}}, fallback, JSON.parse(raw));
    }} catch (e) {{
      return Object.assign({{}}, fallback);
    }}
  }}

  function loadBool(key, fallback) {{
    try {{
      const raw = localStorage.getItem(key);
      if (raw === null || raw === "") return fallback;
      return JSON.parse(raw) !== false;
    }} catch (e) {{
      return fallback;
    }}
  }}

  function saveState(key, state) {{
    localStorage.setItem(key, JSON.stringify(state));
  }}

  function currentSiteState() {{
    const state = {{}};
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{
      state[cb.getAttribute("data-site")] = cb.checked;
    }});
    return state;
  }}

  function currentSiteOrder() {{
    return Array.prototype.map.call(
      document.querySelectorAll("#site-toggles .site-toggle[data-site]"),
      function (el) {{ return el.getAttribute("data-site"); }}
    );
  }}

  function applySiteOrder(order) {{
    const list = Array.isArray(order) ? order.slice() : [];
    const togglesRoot = document.getElementById("site-toggles");
    const toggleById = {{}};
    togglesRoot.querySelectorAll(".site-toggle[data-site]").forEach(function (el) {{
      toggleById[el.getAttribute("data-site")] = el;
    }});
    const seen = {{}};
    list.forEach(function (id) {{
      if (toggleById[id] && !seen[id]) {{
        togglesRoot.appendChild(toggleById[id]);
        seen[id] = true;
      }}
    }});
    Object.keys(toggleById).forEach(function (id) {{
      if (!seen[id]) togglesRoot.appendChild(toggleById[id]);
    }});

    const sectionById = {{}};
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      sectionById[sec.getAttribute("data-site")] = sec;
    }});
    const orderedSections = [];
    const sectionSeen = {{}};
    currentSiteOrder().forEach(function (id) {{
      if (sectionById[id] && !sectionSeen[id]) {{
        orderedSections.push(sectionById[id]);
        sectionSeen[id] = true;
      }}
    }});
    Object.keys(sectionById).forEach(function (id) {{
      if (!sectionSeen[id]) orderedSections.push(sectionById[id]);
    }});
    orderedSections.forEach(function (sec) {{
      const anchor = document.querySelector("footer.page-footer");
      if (anchor) document.body.insertBefore(sec, anchor);
      else document.body.appendChild(sec);
    }});

    const finalOrder = currentSiteOrder();
    const allNewList = document.querySelector("#all-new ol.all-new-list");
    if (allNewList) {{
      const items = Array.prototype.slice.call(
        allNewList.querySelectorAll("li.headline[data-site]")
      );
      const bySite = {{}};
      items.forEach(function (li) {{
        const id = li.getAttribute("data-site");
        if (!bySite[id]) bySite[id] = [];
        bySite[id].push(li);
      }});
      const used = {{}};
      finalOrder.forEach(function (id) {{
        if (!bySite[id] || used[id]) return;
        used[id] = true;
        bySite[id].forEach(function (li) {{ allNewList.appendChild(li); }});
      }});
      Object.keys(bySite).forEach(function (id) {{
        if (used[id]) return;
        bySite[id].forEach(function (li) {{ allNewList.appendChild(li); }});
      }});
    }}
    return finalOrder;
  }}

  function currentCatState() {{
    const state = {{}};
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{
      state[cb.getAttribute("data-category")] = cb.checked;
    }});
    return state;
  }}

  function currentExternalState() {{
    return document.getElementById("toggle-external").checked;
  }}

  function currentOnlyNewState() {{
    return document.getElementById("toggle-only-new").checked;
  }}

  function currentLangState() {{
    const state = {{}};
    document.querySelectorAll("#lang-toggles input[data-lang]").forEach(function (cb) {{
      state[cb.getAttribute("data-lang")] = cb.checked;
    }});
    return state;
  }}

  function langEnabledForSite(siteId, langs) {{
    const code = siteLangMap[siteId];
    if (!code) return true;
    return langs[code] !== false;
  }}

  function applyLanguages(state) {{
    const langs = state || currentLangState();
    document.querySelectorAll("#lang-toggles input[data-lang]").forEach(function (cb) {{
      const code = cb.getAttribute("data-lang");
      cb.checked = langs[code] !== false;
    }});
    document.querySelectorAll("#site-toggles .site-toggle[data-lang]").forEach(function (el) {{
      const code = el.getAttribute("data-lang");
      el.classList.toggle("hidden-lang", langs[code] === false);
    }});
    applySites(currentSiteState());
  }}

  function applySites(state) {{
    const langs = currentLangState();
    document.querySelectorAll("section.site").forEach(function (sec) {{
      const id = sec.getAttribute("data-site");
      const langOn = langEnabledForSite(id, langs);
      sec.classList.toggle("hidden", state[id] === false || !langOn);
    }});
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{
      cb.checked = state[cb.getAttribute("data-site")] !== false;
    }});
    document.querySelectorAll("#all-new li.headline[data-site]").forEach(function (li) {{
      const id = li.getAttribute("data-site");
      const lang = li.getAttribute("data-lang");
      li.classList.toggle("hidden-site", state[id] === false);
      li.classList.toggle("hidden-lang", lang && langs[lang] === false);
    }});
    refreshAllNewCount();
  }}

  function currentCollapsedState() {{
    const state = {{}};
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      state[sec.getAttribute("data-site")] = sec.classList.contains("collapsed");
    }});
    const allNew = document.getElementById("all-new");
    if (allNew) {{
      state["all-new"] = allNew.classList.contains("collapsed");
    }}
    return state;
  }}

  function applyCollapsedSites(state) {{
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      const id = sec.getAttribute("data-site");
      const collapsed = state[id] === true;
      sec.classList.toggle("collapsed", collapsed);
      const btn = sec.querySelector(".site-collapse-toggle");
      if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }});
    const allNew = document.getElementById("all-new");
    if (allNew) {{
      const collapsed = state["all-new"] === true;
      allNew.classList.toggle("collapsed", collapsed);
      const btn = allNew.querySelector(".site-collapse-toggle");
      if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }}
  }}

  document.querySelectorAll("section.site .site-collapse-toggle, section.all-new .site-collapse-toggle").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      const sec = btn.closest("section.site, section.all-new");
      if (!sec) return;
      const collapsed = !sec.classList.contains("collapsed");
      sec.classList.toggle("collapsed", collapsed);
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      saveState(COLLAPSED_KEY, currentCollapsedState());
    }});
  }});

  function refreshAllNewCount() {{
    const section = document.getElementById("all-new");
    if (!section) return;
    const items = section.querySelectorAll("li.headline");
    let visible = 0;
    items.forEach(function (li) {{
      if (
        !li.classList.contains("hidden-site") &&
        !li.classList.contains("hidden-lang") &&
        !li.classList.contains("hidden-cat") &&
        !li.classList.contains("hidden-external") &&
        !li.classList.contains("hidden-exclude")
      ) visible += 1;
    }});
    const el = document.getElementById("all-new-count");
    if (el) el.textContent = "(" + visible + ")";
  }}

  function refreshCollapsedNewCounts() {{
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      const countEl = sec.querySelector(".collapsed-new-count");
      let n = 0;
      sec.querySelectorAll(".site-body li.headline[data-new='1']").forEach(function (li) {{
        if (isFilterVisible(li)) n += 1;
      }});
      if (n <= 0) {{
        if (countEl) countEl.remove();
      }} else {{
        const label = n === 1 ? "1 new article" : n + " new articles";
        if (countEl) countEl.textContent = label;
        else {{
          const span = document.createElement("span");
          span.className = "collapsed-new-count";
          span.textContent = label;
          const title = sec.querySelector("h2.site-title");
          if (title) title.appendChild(span);
        }}
      }}
    }});
  }}

  function applyAllNew(show) {{
    const cb = document.getElementById("toggle-all-new");
    if (cb) cb.checked = !!show;
    const section = document.getElementById("all-new");
    if (section) section.classList.toggle("hidden", !show);
  }}

  function applyDimOpened(dim) {{
    const cb = document.getElementById("toggle-dim-opened");
    if (cb) cb.checked = !!dim;
    document.body.classList.toggle("dim-opened", !!dim);
  }}

  function applyOpenedTodayPanel(show) {{
    const cb = document.getElementById("toggle-opened-today");
    if (cb) cb.checked = !!show;
    const section = document.getElementById("opened-today");
    if (section) section.classList.toggle("hidden", !show);
  }}

  function applyDarkMode(dark) {{
    const on = !!dark;
    document.body.classList.toggle("dark", on);
    const cb = document.getElementById("toggle-dark");
    if (cb) cb.checked = on;
    const btn = document.getElementById("btn-theme");
    if (btn) {{
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "Light" : "Dark";
    }}
  }}

  function loadOpenedLinks() {{
    try {{
      const raw = localStorage.getItem(OPENED_LINKS_KEY);
      if (!raw) return {{}};
      const data = JSON.parse(raw);
      return data && typeof data === "object" ? data : {{}};
    }} catch (e) {{
      return {{}};
    }}
  }}

  function saveOpenedLinks(map) {{
    localStorage.setItem(OPENED_LINKS_KEY, JSON.stringify(map));
  }}

  function pruneOpenedLinks(map, maxAgeDays) {{
    const cutoff = Date.now() - maxAgeDays * 24 * 60 * 60 * 1000;
    const next = {{}};
    Object.keys(map).forEach(function (href) {{
      const ts = Date.parse(map[href]);
      if (!isNaN(ts) && ts >= cutoff) next[href] = map[href];
    }});
    return next;
  }}

  function sameLocalDay(iso) {{
    const ts = Date.parse(iso);
    if (isNaN(ts)) return false;
    const d = new Date(ts);
    const now = new Date();
    return (
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    );
  }}

  function applyOpenedMarks(map) {{
    document.querySelectorAll("li.headline[data-href]").forEach(function (li) {{
      const href = li.getAttribute("data-href");
      li.classList.toggle("opened", !!(href && map[href]));
    }});
  }}

  function rebuildOpenedToday(map) {{
    const list = document.getElementById("opened-today-list");
    const countEl = document.getElementById("opened-today-count");
    if (!list) return;
    const today = [];
    Object.keys(map).forEach(function (href) {{
      if (sameLocalDay(map[href])) today.push({{ href: href, at: map[href] }});
    }});
    today.sort(function (a, b) {{
      return Date.parse(b.at) - Date.parse(a.at);
    }});
    list.innerHTML = "";
    if (!today.length) {{
      list.innerHTML = '<li class="empty">No articles opened today.</li>';
      if (countEl) countEl.textContent = "(0)";
      return;
    }}
    today.forEach(function (item) {{
      let title = item.href;
      let match = null;
      document.querySelectorAll("li.headline[data-href]").forEach(function (li) {{
        if (!match && li.getAttribute("data-href") === item.href) {{
          const a = li.querySelector("a");
          if (a) match = a;
        }}
      }});
      if (match && match.textContent) title = match.textContent;
      const li = document.createElement("li");
      li.className = "headline opened";
      li.setAttribute("data-href", item.href);
      const main = document.createElement("span");
      main.className = "hl-main";
      const a = document.createElement("a");
      a.href = item.href;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = title;
      main.appendChild(a);
      li.appendChild(main);
      list.appendChild(li);
    }});
    if (countEl) countEl.textContent = "(" + today.length + ")";
  }}

  function markOpened(href) {{
    if (!href) return;
    let map = loadOpenedLinks();
    map[href] = new Date().toISOString();
    map = pruneOpenedLinks(map, 30);
    saveOpenedLinks(map);
    applyOpenedMarks(map);
    rebuildOpenedToday(map);
  }}

  function clearAllBrowserData() {{
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {{
      const k = localStorage.key(i);
      if (k && k.indexOf("newsextract.") === 0) keys.push(k);
    }}
    keys.forEach(function (k) {{ localStorage.removeItem(k); }});
    try {{
      const cookies = document.cookie ? document.cookie.split(";") : [];
      cookies.forEach(function (c) {{
        const name = c.split("=")[0].trim();
        if (!name) return;
        document.cookie = name + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
      }});
    }} catch (e) {{}}
  }}

  function applyCats(state) {{
    document.querySelectorAll("li.headline[data-category]").forEach(function (li) {{
      const id = li.getAttribute("data-category");
      li.classList.toggle("hidden-cat", state[id] === false);
    }});
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{
      cb.checked = state[cb.getAttribute("data-category")] !== false;
    }});
    applySeenLimit(currentSeenLimit());
    refreshCollapsedNewCounts();
    refreshAllNewCount();
  }}

  function applyExternal(show) {{
    document.getElementById("toggle-external").checked = !!show;
    document.querySelectorAll("li.headline[data-external='1']").forEach(function (li) {{
      li.classList.toggle("hidden-external", !show);
    }});
    applySeenLimit(currentSeenLimit());
    refreshCollapsedNewCounts();
    refreshAllNewCount();
  }}

  function applyOnlyNew(only) {{
    document.getElementById("toggle-only-new").checked = !!only;
    document.body.classList.toggle("only-new", !!only);
  }}

  function applyBgStrength(value) {{
    const n = Math.max(0, Math.min(100, Number(value) || 0));
    document.documentElement.style.setProperty("--hl-bg-strength", n + "%");
    const slider = document.getElementById("slider-bg-strength");
    const label = document.getElementById("slider-bg-strength-value");
    if (slider) slider.value = String(n);
    if (label) label.textContent = n + "%";
    return n;
  }}

  function currentBgStrength() {{
    return Number(document.getElementById("slider-bg-strength").value) || 0;
  }}

  function isFilterVisible(li) {{
    return (
      !li.classList.contains("hidden-cat") &&
      !li.classList.contains("hidden-external") &&
      !li.classList.contains("hidden-exclude")
    );
  }}

  function applySeenLimit(value) {{
    const n = Math.max(0, Math.min(500, Number(value) || 0));
    const input = document.getElementById("input-seen-limit");
    const label = document.getElementById("input-seen-limit-value");
    if (input) input.value = String(n);
    if (label) label.textContent = String(n);
    document.querySelectorAll(".freshness-block[data-freshness='seen']").forEach(function (block) {{
      const items = Array.prototype.slice.call(
        block.querySelectorAll("li.headline[data-seen-index]")
      );
      items.forEach(function (li) {{
        li.classList.remove("hidden-seen-limit");
      }});
      const visible = items.filter(isFilterVisible);
      const matching = visible.length;
      const pages = n > 0 ? Math.max(1, Math.ceil(matching / n)) : 1;
      let page = Number(block.getAttribute("data-seen-page"));
      if (!Number.isFinite(page) || page < 0) page = 0;
      if (page > pages - 1) page = pages - 1;
      block.setAttribute("data-seen-page", String(page));

      const start = n > 0 ? page * n : 0;
      const end = n > 0 ? start + n : 0;
      visible.forEach(function (li, i) {{
        li.classList.toggle("hidden-seen-limit", i < start || i >= end);
      }});
      const shown = n > 0 ? Math.max(0, Math.min(end, matching) - start) : 0;
      const shownEl = block.querySelector(".seen-shown");
      const totalEl = block.querySelector(".seen-total");
      if (shownEl) shownEl.textContent = String(shown);
      if (totalEl) totalEl.textContent = String(matching);
      block.classList.toggle("hidden-seen-limit-block", n === 0 || matching === 0);

      const hasPages = n > 0 && matching > n;
      block.classList.toggle("has-seen-pages", hasPages);
      const prevBtn = block.querySelector(".seen-prev");
      const nextBtn = block.querySelector(".seen-next");
      const pageLabel = block.querySelector(".seen-page-label");
      if (prevBtn) {{
        prevBtn.textContent = "Previous " + n;
        prevBtn.disabled = !hasPages || page <= 0;
      }}
      if (nextBtn) {{
        nextBtn.textContent = "Next " + n;
        nextBtn.disabled = !hasPages || page >= pages - 1;
      }}
      if (pageLabel) pageLabel.textContent = "(" + (page + 1) + " of " + pages + ")";

      const list = block.querySelector("ol");
      if (list) {{
        const padNeeded = hasPages && shown > 0 && shown < n ? n - shown : 0;
        let pads = list.querySelectorAll("li.seen-pad");
        while (pads.length > padNeeded) {{
          pads[pads.length - 1].remove();
          pads = list.querySelectorAll("li.seen-pad");
        }}
        while (pads.length < padNeeded) {{
          const pad = document.createElement("li");
          pad.className = "seen-pad";
          pad.setAttribute("aria-hidden", "true");
          pad.textContent = "\\u00a0";
          list.appendChild(pad);
          pads = list.querySelectorAll("li.seen-pad");
        }}
      }}
    }});
    return n;
  }}

  document.addEventListener("click", function (e) {{
    const btn = e.target.closest ? e.target.closest(".seen-prev, .seen-next") : null;
    if (!btn) return;
    const block = btn.closest(".freshness-block[data-freshness='seen']");
    if (!block) return;
    const n = currentSeenLimit();
    if (n <= 0) return;
    const matching = Array.prototype.slice
      .call(block.querySelectorAll("li.headline[data-seen-index]"))
      .filter(isFilterVisible).length;
    const pages = Math.max(1, Math.ceil(matching / n));
    let page = Number(block.getAttribute("data-seen-page")) || 0;
    if (btn.classList.contains("seen-prev")) page -= 1;
    else page += 1;
    if (page < 0) page = 0;
    if (page > pages - 1) page = pages - 1;
    block.setAttribute("data-seen-page", String(page));
    applySeenLimit(n);
  }});

  function currentSeenLimit() {{
    return Number(document.getElementById("input-seen-limit").value) || 0;
  }}

  function parseHighlightWords(raw) {{
    const seen = {{}};
    const words = [];
    String(raw || "").split(",").forEach(function (part) {{
      const w = part.replace(/\\s+/g, " ").trim().toLowerCase();
      if (w.length < 2 || seen[w]) return;
      seen[w] = true;
      words.push(w);
    }});
    return words;
  }}

  function currentHighlightWords() {{
    return document.getElementById("input-highlight-words").value || "";
  }}

  function applyHighlightWords(raw) {{
    const input = document.getElementById("input-highlight-words");
    if (input && input.value !== raw) input.value = raw;
    const keywords = parseHighlightWords(raw);
    document.querySelectorAll("li.headline").forEach(function (li) {{
      const a = li.querySelector("a");
      if (!a) return;
      const hay = (a.textContent || "").toLowerCase();
      const hit = keywords.some(function (k) {{ return hay.indexOf(k) !== -1; }});
      li.classList.toggle("hl-keyword", hit);
    }});
    return raw;
  }}

  function currentExcludeWords() {{
    return document.getElementById("input-exclude-words").value || "";
  }}

  function applyExcludeWords(raw) {{
    const input = document.getElementById("input-exclude-words");
    if (input && input.value !== raw) input.value = raw;
    const keywords = parseHighlightWords(raw);
    document.querySelectorAll("li.headline").forEach(function (li) {{
      const a = li.querySelector("a");
      if (!a) return;
      const hay = (a.textContent || "").toLowerCase();
      const hit = keywords.some(function (k) {{ return hay.indexOf(k) !== -1; }});
      li.classList.toggle("hidden-exclude", hit);
    }});
    applySeenLimit(currentSeenLimit());
    refreshCollapsedNewCounts();
    refreshAllNewCount();
    return raw;
  }}

  function setStatus(msg) {{
    document.getElementById("status").textContent = msg || "";
  }}

  function downloadJson(filename, obj) {{
    const blob = new Blob([JSON.stringify(obj, null, 2) + "\\n"], {{ type: "application/json" }});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }}

  applySites(loadState(SITE_KEY, initialSites));
  applyLanguages(loadState(LANG_KEY, initialLanguages));
  applyCats(loadState(CAT_KEY, initialCats));
  applyExternal(loadBool(EXT_KEY, initialExternal));
  applyOnlyNew(loadBool(ONLY_NEW_KEY, initialOnlyNew));
  applyAllNew(loadBool(ALL_NEW_KEY, initialAllNew));
  applyDimOpened(loadBool(DIM_OPENED_KEY, initialDimOpened));
  applyOpenedTodayPanel(loadBool(OPENED_TODAY_KEY, initialOpenedToday));
  applyDarkMode(loadBool(DARK_MODE_KEY, initialDarkMode));
  applyCollapsedSites(loadState(COLLAPSED_KEY, {{}}));
  (function () {{
    let map = pruneOpenedLinks(loadOpenedLinks(), 30);
    saveOpenedLinks(map);
    applyOpenedMarks(map);
    rebuildOpenedToday(map);
  }})();
  (function () {{
    let strength = initialBgStrength;
    try {{
      const raw = localStorage.getItem("newsextract.bgStrength");
      if (raw !== null && raw !== "") strength = Number(JSON.parse(raw));
    }} catch (e) {{}}
    applyBgStrength(strength);
  }})();
  (function () {{
    let limit = initialSeenLimit;
    try {{
      const raw = localStorage.getItem("newsextract.seenLimit");
      if (raw !== null && raw !== "") limit = Number(JSON.parse(raw));
    }} catch (e) {{}}
    applySeenLimit(limit);
  }})();
  (function () {{
    let words = initialHighlightWords;
    try {{
      const raw = localStorage.getItem("newsextract.highlightWords");
      if (raw !== null && raw !== "") words = JSON.parse(raw);
    }} catch (e) {{}}
    applyHighlightWords(typeof words === "string" ? words : "");
  }})();
  (function () {{
    let words = initialExcludeWords;
    try {{
      const raw = localStorage.getItem("newsextract.excludeWords");
      if (raw !== null && raw !== "") words = JSON.parse(raw);
    }} catch (e) {{}}
    applyExcludeWords(typeof words === "string" ? words : "");
  }})();
  (function () {{
    let order = initialSiteOrder;
    try {{
      const raw = localStorage.getItem("newsextract.siteOrder");
      if (raw !== null && raw !== "") {{
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length) order = parsed;
      }}
    }} catch (e) {{}}
    applySiteOrder(order);
  }})();

  (function setupSiteDragDrop() {{
    const root = document.getElementById("site-toggles");
    let dragEl = null;

    function rowFromEvent(e) {{
      const el = e.target.closest ? e.target.closest(".site-toggle[data-site]") : null;
      return el && root.contains(el) ? el : null;
    }}

    root.addEventListener("dragstart", function (e) {{
      const row = rowFromEvent(e);
      if (!row) return;
      if (e.target && e.target.closest && e.target.closest("input")) {{
        e.preventDefault();
        return;
      }}
      dragEl = row;
      row.classList.add("dragging");
      try {{
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", row.getAttribute("data-site") || "");
      }} catch (err) {{}}
    }});

    root.addEventListener("dragend", function () {{
      if (dragEl) dragEl.classList.remove("dragging");
      root.querySelectorAll(".site-toggle.drag-over").forEach(function (el) {{
        el.classList.remove("drag-over");
      }});
      dragEl = null;
      const order = applySiteOrder(currentSiteOrder());
      saveState("newsextract.siteOrder", order);
      setStatus("Site order updated (browser only)");
    }});

    root.addEventListener("dragover", function (e) {{
      if (!dragEl) return;
      e.preventDefault();
      const over = rowFromEvent(e);
      if (!over || over === dragEl) return;
      root.querySelectorAll(".site-toggle.drag-over").forEach(function (el) {{
        if (el !== over) el.classList.remove("drag-over");
      }});
      over.classList.add("drag-over");
      const rect = over.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      if (before) root.insertBefore(dragEl, over);
      else root.insertBefore(dragEl, over.nextSibling);
    }});

    root.addEventListener("drop", function (e) {{
      e.preventDefault();
    }});
  }})();

  document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      const state = currentSiteState();
      saveState(SITE_KEY, state);
      applySites(state);
      setStatus("Site view updated (browser only)");
    }});
  }});

  document.querySelectorAll("#lang-toggles input[data-lang]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      const state = currentLangState();
      saveState(LANG_KEY, state);
      applyLanguages(state);
      setStatus("Language filter updated (browser only)");
    }});
  }});

  document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      const state = currentCatState();
      saveState(CAT_KEY, state);
      applyCats(state);
      setStatus("Category view updated (browser only)");
    }});
  }});

  document.getElementById("toggle-external").addEventListener("change", function () {{
    const show = currentExternalState();
    saveState(EXT_KEY, show);
    applyExternal(show);
    setStatus("Off-site link view updated (browser only)");
  }});

  document.getElementById("toggle-only-new").addEventListener("change", function () {{
    const only = currentOnlyNewState();
    saveState(ONLY_NEW_KEY, only);
    applyOnlyNew(only);
    setStatus(only ? "Showing only new articles" : "Showing new and previously seen");
  }});

  document.getElementById("toggle-all-new").addEventListener("change", function () {{
    const show = document.getElementById("toggle-all-new").checked;
    saveState(ALL_NEW_KEY, show);
    applyAllNew(show);
    setStatus(show ? "All new feed shown" : "All new feed hidden");
  }});

  document.getElementById("toggle-dim-opened").addEventListener("change", function () {{
    const dim = document.getElementById("toggle-dim-opened").checked;
    saveState(DIM_OPENED_KEY, dim);
    applyDimOpened(dim);
    setStatus(dim ? "Opened links dimmed" : "Opened links not dimmed");
  }});

  document.getElementById("toggle-opened-today").addEventListener("change", function () {{
    const show = document.getElementById("toggle-opened-today").checked;
    saveState(OPENED_TODAY_KEY, show);
    applyOpenedTodayPanel(show);
    setStatus(show ? "Opened today shown" : "Opened today hidden");
  }});

  document.getElementById("toggle-dark").addEventListener("change", function () {{
    const dark = document.getElementById("toggle-dark").checked;
    saveState(DARK_MODE_KEY, dark);
    applyDarkMode(dark);
    setStatus(dark ? "Dark mode on" : "Light mode on");
  }});

  document.getElementById("btn-theme").addEventListener("click", function () {{
    const dark = !document.body.classList.contains("dark");
    saveState(DARK_MODE_KEY, dark);
    applyDarkMode(dark);
    setStatus(dark ? "Dark mode on" : "Light mode on");
  }});

  document.addEventListener("click", function (e) {{
    const a = e.target && e.target.closest ? e.target.closest("li.headline a") : null;
    if (!a) return;
    const li = a.closest("li.headline");
    const href = (li && li.getAttribute("data-href")) || a.href;
    markOpened(href);
  }});

  document.getElementById("btn-clear-storage").addEventListener("click", function () {{
    if (!confirm("Clear all NewsExtract browser settings, opened-link history, and cookies for this page?")) return;
    clearAllBrowserData();
    setStatus("Cleared — reloading…");
    location.reload();
  }});

  document.getElementById("slider-bg-strength").addEventListener("input", function () {{
    const n = applyBgStrength(currentBgStrength());
    saveState("newsextract.bgStrength", n);
    setStatus("Color strength " + n + "%");
  }});

  document.getElementById("input-seen-limit").addEventListener("input", function () {{
    const n = applySeenLimit(currentSeenLimit());
    saveState("newsextract.seenLimit", n);
    setStatus("Showing up to " + n + " previously seen per site");
  }});

  document.getElementById("input-highlight-words").addEventListener("input", function () {{
    const words = applyHighlightWords(currentHighlightWords());
    saveState("newsextract.highlightWords", words);
    const n = parseHighlightWords(words).length;
    setStatus(n ? ("Highlighting " + n + " keyword" + (n === 1 ? "" : "s")) : "Keyword highlight cleared");
  }});

  document.getElementById("input-exclude-words").addEventListener("input", function () {{
    const words = applyExcludeWords(currentExcludeWords());
    saveState("newsextract.excludeWords", words);
    const n = parseHighlightWords(words).length;
    setStatus(n ? ("Excluding " + n + " keyword" + (n === 1 ? "" : "s")) : "Exclude filter cleared");
  }});

  document.getElementById("btn-sites-all").addEventListener("click", function () {{
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{ cb.checked = true; }});
    const state = currentSiteState();
    saveState(SITE_KEY, state);
    applySites(state);
  }});

  document.getElementById("btn-sites-none").addEventListener("click", function () {{
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{ cb.checked = false; }});
    const state = currentSiteState();
    saveState(SITE_KEY, state);
    applySites(state);
  }});

  document.getElementById("btn-cats-all").addEventListener("click", function () {{
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{ cb.checked = true; }});
    const state = currentCatState();
    saveState(CAT_KEY, state);
    applyCats(state);
  }});

  document.getElementById("btn-cats-none").addEventListener("click", function () {{
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{ cb.checked = false; }});
    const state = currentCatState();
    saveState(CAT_KEY, state);
    applyCats(state);
  }});

  document.getElementById("btn-export").addEventListener("click", function () {{
    const siteState = currentSiteState();
    const catState = currentCatState();
    const showExternal = currentExternalState();
    const onlyNew = currentOnlyNewState();
    const bgStrength = currentBgStrength();
    const seenLimit = currentSeenLimit();
    const highlightWords = currentHighlightWords();
    const excludeWords = currentExcludeWords();
    const siteOrder = currentSiteOrder();
    const showAllNew = document.getElementById("toggle-all-new").checked;
    const dimOpened = document.getElementById("toggle-dim-opened").checked;
    const showOpenedToday = document.getElementById("toggle-opened-today").checked;
    const darkMode = document.getElementById("toggle-dark").checked;
    const languages = currentLangState();

    const nextSites = JSON.parse(JSON.stringify(sitesConfig));
    Object.keys(siteState).forEach(function (id) {{
      if (!nextSites[id] || typeof nextSites[id] !== "object") nextSites[id] = {{}};
      nextSites[id].enabled = siteState[id] !== false;
    }});
    delete nextSites._settings;

    const nextSettings = JSON.parse(JSON.stringify(settingsConfig || {{}}));
    nextSettings.show_external = showExternal;
    nextSettings.only_new = onlyNew;
    nextSettings.bg_strength = bgStrength;
    nextSettings.seen_limit = seenLimit;
    nextSettings.highlight_words = highlightWords;
    nextSettings.exclude_words = excludeWords;
    nextSettings.site_order = siteOrder;
    nextSettings.show_all_new = showAllNew;
    nextSettings.dim_opened = dimOpened;
    nextSettings.show_opened_today = showOpenedToday;
    nextSettings.dark_mode = darkMode;
    nextSettings.languages = languages;

    const nextCats = JSON.parse(JSON.stringify(categoriesConfig));
    Object.keys(catState).forEach(function (id) {{
      if (!nextCats[id] || typeof nextCats[id] !== "object") nextCats[id] = {{ label: id, color: "#eee", match: [] }};
      nextCats[id].enabled = catState[id] !== false;
    }});

    downloadJson("sites.json", nextSites);
    setTimeout(function () {{
      downloadJson("settings.json", nextSettings);
    }}, 400);
    setTimeout(function () {{
      downloadJson("categories.json", nextCats);
      setStatus("Downloaded sites.json + settings.json + categories.json — replace project files for next run");
    }}, 800);
  }});
}})();
</script>
  <footer class="page-footer">NewsExtract - by Theo Engell, only front pages are read, cookies are used to store your preferences</footer>
</body>
</html>
"""


def main():
    parser = argparse.ArgumentParser(
        description="Extract headlines from enabled site parsers (cache by default; --update to fetch)."
    )
    parser.add_argument(
        "--list-sites",
        action="store_true",
        help="List discovered site parsers (and enabled flag) and exit",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated site ids to run (still respects sites.json enabled unless listed here)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print full headline lists; default is a short new/seen count per site",
    )
    parser.add_argument("--min-len", type=int, default=8, help="Minimum headline length (default: 8)")
    parser.add_argument("--max-len", type=int, default=200, help="Maximum headline length (default: 200)")
    parser.add_argument("-o", "--output", help="Write full text headlines to this file")
    parser.add_argument("--with-links", action="store_true", help="Also print the article URL next to each title (implies --verbose)")
    parser.add_argument("--limit", type=int, default=None, help="Only show the first N headlines per site")
    parser.add_argument(
        "--html",
        nargs="?",
        const=DEFAULT_HTML_FILE,
        default=DEFAULT_HTML_FILE,
        metavar="PATH",
        help=f"Write combined HTML dashboard and open it (default: {DEFAULT_HTML_FILE})",
    )
    parser.add_argument(
        "--no-html",
        action="store_true",
        help="Don't write or open the HTML dashboard",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Fetch fresh headlines from the web and update the cache "
             "(default: rebuild HTML/console output from cache only)",
    )
    parser.add_argument(
        "--cache-file",
        default=DEFAULT_CACHE_FILE,
        help=f"JSON cache of previously-seen headlines (default: {DEFAULT_CACHE_FILE})",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Don't read or update the cache (requires --update)",
    )
    parser.add_argument(
        "--categories",
        default=DEFAULT_CATEGORIES_FILE,
        help=f"Category definitions (label/color/match/enabled) for HTML "
             f"(default: {DEFAULT_CATEGORIES_FILE})",
    )
    parser.add_argument(
        "--sites-file",
        default=DEFAULT_SITES_FILE,
        help=f"Per-site enabled flags (default: {DEFAULT_SITES_FILE})",
    )
    parser.add_argument(
        "--settings-file",
        default=DEFAULT_SETTINGS_FILE,
        help=f"Global UI settings (default: {DEFAULT_SETTINGS_FILE})",
    )
    parser.add_argument(
        "--parser-engine",
        choices=("py", "grammar", "both"),
        default=_DEFAULT_PARSER_ENGINE,
        help="Headline extract engine: YAML grammar (grammar, default), "
             "legacy alias py, or both (self-check; uses primary output)",
    )
    args = parser.parse_args()
    verbose = args.verbose or args.with_links
    parsers_pkg.PARSER_ENGINE = args.parser_engine

    refresh()
    parsers = list(PARSERS)
    if not parsers:
        print(
            "No site parsers found in parsers/grammar/. "
            "Add a YAML grammar with id/name/url/strategies.",
            file=sys.stderr,
        )
        sys.exit(1)

    settings = load_settings_config(
        args.settings_file, parsers, sites_path=args.sites_file
    )
    sites_config = load_sites_config(args.sites_file, parsers)
    categories = load_categories(args.categories)

    if args.list_sites:
        for site_id, name, url in list_sites():
            enabled = sites_config.get(site_id, {}).get("enabled", True)
            flag = "on" if enabled else "off"
            print(f"{site_id}\t{flag}\t{name}\t{url}")
        return

    only_ids = None
    if args.only:
        only_ids = [s.strip() for s in args.only.split(",") if s.strip()]
        for sid in only_ids:
            try:
                get_parser_by_id(sid)
            except UnsupportedSiteError as e:
                print(f"Error: {e}", file=sys.stderr)
                sys.exit(1)

    to_run = []
    languages = normalize_languages(
        settings.get("languages"),
        [str(getattr(m, "LANGUAGE", "da") or "da").strip().lower() or "da" for m in parsers],
    )
    for mod in parsers:
        if only_ids is not None and mod.SITE_ID not in only_ids:
            continue
        if only_ids is None and not sites_config.get(mod.SITE_ID, {}).get("enabled", True):
            print(f"Skipping {mod.SITE_ID} (disabled in {args.sites_file})", file=sys.stderr)
            continue
        lang = str(getattr(mod, "LANGUAGE", "da") or "da").strip().lower() or "da"
        if only_ids is None and languages.get(lang, True) is False:
            print(f"Skipping {mod.SITE_ID} (language '{lang}' disabled)", file=sys.stderr)
            continue
        to_run.append(mod)

    run_order = normalize_site_order(settings.get("site_order"), [m.SITE_ID for m in to_run])
    order_rank = {sid: i for i, sid in enumerate(run_order)}
    to_run.sort(key=lambda m: (order_rank.get(m.SITE_ID, 10_000), m.SITE_ID))

    if not to_run:
        print("No enabled sites to run. Enable sites in sites.json or use --only.", file=sys.stderr)
        sys.exit(1)

    if args.no_cache and not args.update:
        print("Error: --no-cache requires --update (otherwise there is nothing to load).", file=sys.stderr)
        sys.exit(1)

    cache = {} if args.no_cache else load_cache(args.cache_file)
    if not args.update and not cache:
        print(
            f"No cache found at {args.cache_file}. Run with --update first.",
            file=sys.stderr,
        )
        sys.exit(1)

    all_output_parts = []
    site_blocks = []
    page_html_by_site = {}

    for mod in to_run:
        url = sites_config.get(mod.SITE_ID, {}).get("url") or mod.DEFAULT_URL
        error = None
        results = []
        page_html = None

        if not args.update:
            new_results, seen_results, is_first_run, ok = load_results_from_cache(
                cache, url, limit=args.limit
            )
            if not ok:
                error = "No cached data — run with --update"
                new_results, seen_results, is_first_run = [], [], True
                no_cache_mode = True
                output_text, note = f"(failed: {error})", "(0 headlines - no cache)"
                summary = f"{mod.NAME}: no cache (run with --update)"
            else:
                results = new_results + seen_results
                no_cache_mode = False
                output_text, note = format_site_output(
                    results, new_results, seen_results, is_first_run, no_cache_mode, args.with_links
                )
                if is_first_run:
                    summary = f"{mod.NAME}: {len(results)} headlines (cached)"
                else:
                    summary = (
                        f"{mod.NAME}: {len(new_results)} new, "
                        f"{len(seen_results)} previously seen (cached)"
                    )
        else:
            try:
                results, url, page_html = extract_from_site(
                    mod,
                    args.min_len,
                    args.max_len,
                    args.limit,
                    url=url,
                    parser_engine=args.parser_engine,
                )
            except requests.RequestException as e:
                print(f"Error fetching {url}: {e}", file=sys.stderr)
                results = []
                error = str(e)

            if error:
                new_results, seen_results, is_first_run = [], [], True
                no_cache_mode = True
                output_text, note = f"(failed: {error})", f"(0 headlines - fetch failed)"
                summary = f"{mod.NAME}: failed ({error})"
            elif args.no_cache:
                new_results, seen_results, is_first_run = [], results, False
                no_cache_mode = True
                output_text, note = format_site_output(
                    results, new_results, seen_results, is_first_run, no_cache_mode, args.with_links
                )
                summary = f"{mod.NAME}: {len(results)} headlines"
            else:
                new_results, seen_results, is_first_run = split_new_vs_seen(results, url, cache)
                no_cache_mode = False
                output_text, note = format_site_output(
                    results, new_results, seen_results, is_first_run, no_cache_mode, args.with_links
                )
                if is_first_run:
                    summary = f"{mod.NAME}: {len(results)} headlines (first run)"
                else:
                    summary = (
                        f"{mod.NAME}: {len(new_results)} new, "
                        f"{len(seen_results)} previously seen"
                    )

        header = f"##### {mod.NAME} ({url}) #####"
        block = f"{header}\n{output_text}"
        if verbose:
            print(block)
            print(f"{mod.SITE_ID}: {note}", file=sys.stderr)
        else:
            print(summary)
        all_output_parts.append(block)

        if page_html:
            page_html_by_site[mod.SITE_ID] = page_html

        site_blocks.append({
            "site_id": mod.SITE_ID,
            "name": mod.NAME,
            "url": url,
            "language": str(getattr(mod, "LANGUAGE", "da") or "da").strip().lower() or "da",
            "new_results": new_results,
            "seen_results": seen_results if (is_first_run or not no_cache_mode) else results,
            "is_first_run": is_first_run or no_cache_mode,
            "error": error,
        })
        # For no-cache mode, show everything under first-run style in HTML
        if no_cache_mode and not error:
            site_blocks[-1]["seen_results"] = results
            site_blocks[-1]["new_results"] = []
            site_blocks[-1]["is_first_run"] = True

    if args.update and not args.no_cache:
        save_cache(args.cache_file, cache)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n\n".join(all_output_parts) + "\n")

    # Learn new categories from unmatched headline URLs and persist them.
    all_hrefs = []
    for block in site_blocks:
        for _text, href in block.get("new_results") or []:
            all_hrefs.append(href)
        for _text, href in block.get("seen_results") or []:
            all_hrefs.append(href)
    discovered = discover_categories_from_hrefs(all_hrefs, categories)
    if discovered:
        save_categories(args.categories, categories)
        print(
            f"Added {len(discovered)} new categor{'y' if len(discovered) == 1 else 'ies'} "
            f"to {args.categories}: {', '.join(discovered)}",
            file=sys.stderr,
        )

    if not args.no_html:
        # Include disabled sites as empty/hidden sections so toggles can turn them on visually
        # (they won't have fresh data until next fetch when enabled).
        present_ids = {b["site_id"] for b in site_blocks}
        for mod in parsers:
            if mod.SITE_ID in present_ids:
                continue
            lang = str(getattr(mod, "LANGUAGE", "da") or "da").strip().lower() or "da"
            if languages.get(lang, True) is False:
                err = f"Language '{language_label(lang)}' disabled — enable it in Settings."
            else:
                err = "Disabled in sites.json — enable and run with --update to fetch."
            site_blocks.append({
                "site_id": mod.SITE_ID,
                "name": mod.NAME,
                "url": mod.DEFAULT_URL,
                "language": lang,
                "new_results": [],
                "seen_results": [],
                "is_first_run": True,
                "error": err,
            })
        html_order = normalize_site_order(
            settings.get("site_order"),
            [b["site_id"] for b in site_blocks],
        )
        html_rank = {sid: i for i, sid in enumerate(html_order)}
        site_blocks.sort(
            key=lambda b: (html_rank.get(b["site_id"], 10_000), b["site_id"])
        )

        for block in site_blocks:
            sid = block["site_id"]
            logo = ensure_site_logo(
                sid,
                block["url"],
                logos_dir=DEFAULT_LOGOS_DIR,
                page_html=page_html_by_site.get(sid),
                refresh=bool(args.update and page_html_by_site.get(sid)),
            )
            if logo:
                block["logo"] = logo

        site_domains = {
            mod.SITE_ID: tuple(getattr(mod, "DOMAINS", ()) or ())
            for mod in parsers
        }
        page = build_combined_html(
            site_blocks, categories, sites_config, settings, site_domains
        )
        out_path = args.html or DEFAULT_HTML_FILE
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page)
        abs_path = os.path.abspath(out_path)
        webbrowser.open(f"file://{abs_path}")
        print(f"Opened {abs_path}", file=sys.stderr)


if __name__ == "__main__":
    _started = time.perf_counter()
    try:
        main()
    finally:
        print(f"Elapsed: {time.perf_counter() - _started:.2f}s", file=sys.stderr)
