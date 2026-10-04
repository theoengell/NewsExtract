"""Load/save sites.json, settings.json, and categories.json."""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from urllib.parse import unquote, urlparse

from .cache import clamp_cache_ttl_days
from .constants import (
    DEFAULT_CACHE_TTL_DAYS,
    DEFAULT_CATEGORIES,
    KNOWN_LANGUAGES,
    LEGACY_COLOR_MAP_FILE,
    make_default_settings,
)


def _ensure_parent_dir(path):
    parent = os.path.dirname(path or "")
    if parent:
        os.makedirs(parent, exist_ok=True)

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
        _ensure_parent_dir(path)
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
        _ensure_parent_dir(path)
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

    if not isinstance(settings.get("developer_mode"), bool):
        settings["developer_mode"] = False
        changed = True
    ttl_days = clamp_cache_ttl_days(
        settings.get("cache_ttl_days", DEFAULT_CACHE_TTL_DAYS)
    )
    if settings.get("cache_ttl_days") != ttl_days:
        settings["cache_ttl_days"] = ttl_days
        changed = True

    if changed or not os.path.exists(path):
        save_settings_config(path, settings)
        print(f"Updated {path}.", file=sys.stderr)

    return settings


def save_settings_config(path, config):
    try:
        _ensure_parent_dir(path)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
            f.write("\n")
    except OSError as e:
        print(f"Warning: couldn't write {path} ({e}).", file=sys.stderr)


def load_cluster_ignore_words(path=None):
    """
    Load per-language ignore-word lists for clustering.

    Returns dict of language code -> [str, ...] with ~1000 words each
    (typically en/da/sv/no). Missing/invalid files yield empty lists
    (clustering still works, just noisier).
    """
    from .constants import DEFAULT_CLUSTER_IGNORE_FILE, KNOWN_LANGUAGES

    path = path or DEFAULT_CLUSTER_IGNORE_FILE
    empty = {code: [] for code in KNOWN_LANGUAGES}
    if not path or not os.path.exists(path):
        print(
            f"Warning: cluster ignore words not found at {path}.",
            file=sys.stderr,
        )
        return empty
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Warning: couldn't read {path} ({e}).", file=sys.stderr)
        return empty
    if not isinstance(data, dict):
        return empty

    lang_keys = [
        code
        for code in list(KNOWN_LANGUAGES) + [k for k in data if k != "description"]
        if isinstance(code, str) and code != "description"
    ]
    # De-dupe, preserve order
    seen_langs = set()
    langs = []
    for code in lang_keys:
        code = code.strip().lower()
        if not code or code in seen_langs:
            continue
        seen_langs.add(code)
        langs.append(code)

    out = {}
    for lang in langs:
        raw = data.get(lang, [])
        if not isinstance(raw, list):
            raw = []
        seen = set()
        words = []
        for item in raw:
            w = str(item or "").strip().casefold()
            if len(w) < 2 or w in seen:
                continue
            seen.add(w)
            words.append(w)
        out[lang] = words
    return out


def cluster_ignore_lookup(ignore_by_lang):
    """Flatten per-language lists into a single casefolded membership dict for JS."""
    lookup = {}
    if not isinstance(ignore_by_lang, dict):
        return lookup
    for words in ignore_by_lang.values():
        if not isinstance(words, list):
            continue
        for w in words:
            key = str(w or "").strip().casefold()
            if len(key) >= 2:
                lookup[key] = 1
    return lookup
