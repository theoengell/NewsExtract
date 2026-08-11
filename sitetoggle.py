#!/usr/bin/env python3
"""
sitetoggle.py — turn news sites (or whole languages / countries) on/off.

Site enablement lives in config/sites.json. Language toggles also update
config/settings.json so the dashboard language filters stay in sync.

Usage:
    python sitetoggle.py                 # interactive list + toggle
    python sitetoggle.py bt dr           # toggle those site ids
    python sitetoggle.py on bt           # enable a site
    python sitetoggle.py off guardian    # disable a site
    python sitetoggle.py off se          # disable all Swedish (country) sites
    python sitetoggle.py on danish       # enable all Danish-language sites
    python sitetoggle.py off lang:en     # disable English language + its sites
    python sitetoggle.py on country:gb   # enable all UK sites
    python sitetoggle.py --list          # show status only
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict

from parsers import PARSERS, refresh
from nex.config import (
    language_label,
    load_settings_config,
    load_sites_config,
    normalize_languages,
    save_settings_config,
    save_sites_config,
)
from nex.constants import DEFAULT_SETTINGS_FILE, DEFAULT_SITES_FILE, KNOWN_LANGUAGES


def _flag(enabled: bool) -> str:
    return "ON " if enabled else "OFF"


def _site_enabled(config: dict, site_id: str) -> bool:
    entry = config.get(site_id)
    if isinstance(entry, dict):
        return bool(entry.get("enabled", True))
    return True


def _group_flag(config: dict, site_ids: list[str]) -> str:
    """Return ON / OFF / MIX for a group of sites."""
    if not site_ids:
        return "ON "
    flags = [_site_enabled(config, sid) for sid in site_ids]
    if all(flags):
        return "ON "
    if not any(flags):
        return "OFF"
    return "MIX"


def _site_rows(config: dict) -> list[dict]:
    """Ordered site info dicts for known parsers."""
    rows = []
    for mod in PARSERS:
        sid = mod.SITE_ID
        entry = config.get(sid) if isinstance(config.get(sid), dict) else {}
        name = entry.get("name") or getattr(mod, "NAME", sid)
        rows.append({
            "id": sid,
            "name": name,
            "enabled": _site_enabled(config, sid),
            "language": str(getattr(mod, "LANGUAGE", "") or "").strip().lower(),
            "country": str(getattr(mod, "COUNTRY", "") or "").strip().lower(),
            "country_name": str(getattr(mod, "COUNTRY_NAME", "") or "").strip(),
        })
    return rows


def _countries(rows: list[dict]) -> list[tuple[str, str, list[str]]]:
    """[(country_code, country_name, [site_ids]), ...] sorted by code."""
    by_code: dict[str, list[str]] = defaultdict(list)
    names: dict[str, str] = {}
    for row in rows:
        code = row["country"]
        if not code:
            continue
        by_code[code].append(row["id"])
        if row["country_name"]:
            names[code] = row["country_name"]
    return [
        (code, names.get(code, code), by_code[code])
        for code in sorted(by_code)
    ]


def _languages(rows: list[dict]) -> list[tuple[str, str, list[str]]]:
    """[(lang_code, label, [site_ids]), ...] sorted by code."""
    by_code: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        code = row["language"]
        if not code:
            continue
        by_code[code].append(row["id"])
    return [
        (code, language_label(code), by_code[code])
        for code in sorted(by_code)
    ]


def _print_overview(config: dict, rows: list[dict], settings: dict | None = None) -> None:
    langs = _languages(rows)
    countries = _countries(rows)
    lang_settings = {}
    if isinstance(settings, dict):
        lang_settings = normalize_languages(
            settings.get("languages"),
            [code for code, _, _ in langs],
        )

    print("Languages:")
    for code, label, ids in langs:
        sites_flag = _group_flag(config, ids).strip()
        if lang_settings:
            setting_on = bool(lang_settings.get(code, True))
            setting_flag = "ON" if setting_on else "OFF"
            if setting_flag != sites_flag and sites_flag != "MIX":
                flag = "MIX"
            elif sites_flag == "MIX":
                flag = "MIX"
            else:
                flag = setting_flag
        else:
            flag = sites_flag
        print(f"  lang:{code:<4} [{flag:<3}]  {label}  ({len(ids)} sites)")

    print("Countries:")
    for code, name, ids in countries:
        flag = _group_flag(config, ids).strip()
        print(f"  country:{code:<4} [{flag:<3}]  {name}  ({len(ids)} sites)")

    print("Sites:")
    width = max((len(r["id"]) for r in rows), default=8)
    for i, row in enumerate(rows, start=1):
        meta = ""
        if row["country"] or row["language"]:
            meta = f"  ({row['country'] or '?'} / {row['language'] or '?'})"
        print(
            f"  {i:2d}. [{_flag(row['enabled'])}]  "
            f"{row['id']:<{width}}  {row['name']}{meta}"
        )


def _apply_site(config: dict, site_id: str, mode: str) -> bool:
    """mode: toggle | on | off. Returns new enabled state."""
    entry = config.get(site_id)
    if not isinstance(entry, dict):
        entry = {"enabled": True}
        config[site_id] = entry
    current = bool(entry.get("enabled", True))
    if mode == "on":
        new = True
    elif mode == "off":
        new = False
    else:
        new = not current
    entry["enabled"] = new
    return new


def _bulk_mode(config: dict, site_ids: list[str], mode: str) -> str:
    """
    Resolve effective on/off for a group.
    toggle: if any site is enabled → off, else → on (uniform end state).
    """
    if mode in ("on", "off"):
        return mode
    any_on = any(_site_enabled(config, sid) for sid in site_ids)
    return "off" if any_on else "on"


def _apply_sites(config: dict, site_ids: list[str], mode: str) -> list[tuple[str, bool]]:
    effective = _bulk_mode(config, site_ids, mode)
    changed = []
    for sid in site_ids:
        before = _site_enabled(config, sid)
        after = _apply_site(config, sid, effective)
        if before != after or mode in ("on", "off"):
            changed.append((sid, after))
    return changed


def _set_language_setting(settings: dict, lang: str, enabled: bool) -> None:
    langs = settings.setdefault("languages", {})
    if not isinstance(langs, dict):
        langs = {}
        settings["languages"] = langs
    langs[lang] = bool(enabled)


def _resolve_targets(
    token: str,
    rows: list[dict],
    countries: list[tuple[str, str, list[str]]],
    languages: list[tuple[str, str, list[str]]],
) -> tuple[str, list[str], str | None] | None:
    """
    Resolve a token to (kind, site_ids, language_code_or_None).
    kind is 'site' | 'country' | 'language'.
    """
    token = token.strip()
    if not token:
        return None
    lower = token.lower()

    # Explicit prefixes
    if lower.startswith("lang:") or lower.startswith("language:"):
        code = lower.split(":", 1)[1].strip()
        for lang, label, ids in languages:
            if lang == code or label.lower() == code:
                return ("language", list(ids), lang)
        return None
    if lower.startswith("country:") or lower.startswith("c:"):
        code = lower.split(":", 1)[1].strip()
        for country, name, ids in countries:
            if country == code or name.lower() == code:
                return ("country", list(ids), None)
        return None

    # Numeric site index
    if token.isdigit():
        n = int(token)
        if 1 <= n <= len(rows):
            row = rows[n - 1]
            return ("site", [row["id"]], None)
        return None

    # Exact site id or display name
    for row in rows:
        if row["id"].lower() == lower or row["name"].lower() == lower:
            return ("site", [row["id"]], None)

    # Country code or name (before language so "Norway" is country)
    for country, name, ids in countries:
        if country == lower or name.lower() == lower:
            return ("country", list(ids), None)

    # Language code or label
    for lang, label, ids in languages:
        if lang == lower or label.lower() == lower:
            return ("language", list(ids), lang)

    # Unique site id prefix
    hits = [row["id"] for row in rows if row["id"].lower().startswith(lower)]
    if len(hits) == 1:
        return ("site", hits, None)

    # Unique country / language prefix
    c_hits = [(c, ids) for c, name, ids in countries if c.startswith(lower) or name.lower().startswith(lower)]
    if len(c_hits) == 1:
        return ("country", list(c_hits[0][1]), None)
    l_hits = [(lang, ids) for lang, label, ids in languages if lang.startswith(lower) or label.lower().startswith(lower)]
    if len(l_hits) == 1:
        return ("language", list(l_hits[0][1]), l_hits[0][0])

    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Toggle site enabled flags in sites.json. "
            "Targets may be site ids, countries (dk, se, Norway, country:gb), "
            "or languages (da, Swedish, lang:en)."
        ),
    )
    parser.add_argument(
        "--sites-file",
        default=DEFAULT_SITES_FILE,
        help=f"sites.json path (default: {DEFAULT_SITES_FILE})",
    )
    parser.add_argument(
        "--settings-file",
        default=DEFAULT_SETTINGS_FILE,
        help=f"settings.json path (default: {DEFAULT_SETTINGS_FILE})",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List languages, countries, and sites, then exit",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Site/country/language tokens, optionally starting with on|off",
    )
    args = parser.parse_args()

    refresh()
    if not PARSERS:
        print("No site parsers found in parsers/grammar/<country>/.", file=sys.stderr)
        return 1

    config = load_sites_config(args.sites_file, list(PARSERS))
    settings = load_settings_config(args.settings_file, list(PARSERS), sites_path=args.sites_file)
    rows = _site_rows(config)
    countries = _countries(rows)
    languages = _languages(rows)

    if args.list or not args.targets:
        print(f"Status ({args.sites_file}):")
        _print_overview(config, rows, settings)
        if args.list:
            return 0
        print()
        print("What do you want to toggle? Type one or more of:")
        print("  • a site number or id     e.g. 3   or   bt")
        print("  • a country               e.g. se  or   Sweden")
        print("  • a language              e.g. da  or   Danish")
        print("Separate with spaces or commas. Press Enter alone or q to quit.")
        try:
            raw = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not raw or raw.lower() in ("q", "quit", "exit"):
            return 0
        tokens = [t for part in raw.replace(",", " ").split() for t in [part] if t]
        mode = "toggle"
    else:
        tokens = list(args.targets)
        mode = "toggle"
        if tokens and tokens[0].lower() in ("on", "off", "toggle"):
            mode = tokens.pop(0).lower()

    if not tokens:
        print("No targets specified.", file=sys.stderr)
        return 1

    all_changed: list[tuple[str, bool]] = []
    lang_setting_changes: list[tuple[str, bool]] = []
    seen_sites: set[str] = set()

    for token in tokens:
        resolved = _resolve_targets(token, rows, countries, languages)
        if not resolved:
            print(f"Unknown target: {token}", file=sys.stderr)
            print(
                "Try a site number/id (bt), a country (se / Sweden), "
                "or a language (da / Danish).",
                file=sys.stderr,
            )
            return 1
        kind, site_ids, lang_code = resolved
        effective = _bulk_mode(config, site_ids, mode)

        fresh = [sid for sid in site_ids if sid not in seen_sites]
        for sid in site_ids:
            seen_sites.add(sid)
        if not fresh and kind == "site":
            fresh = list(site_ids)
        if fresh:
            all_changed.extend(_apply_sites(config, fresh, effective))

        if kind == "language" and lang_code:
            _set_language_setting(settings, lang_code, effective == "on")
            lang_setting_changes.append((lang_code, effective == "on"))
        elif kind == "country":
            # Sync language setting when this country owns that language exclusively
            # (e.g. se→sv, dk→da, no→no). Shared languages like en stay untouched.
            country_langs = {
                row["language"] for row in rows if row["id"] in site_ids and row["language"]
            }
            if len(country_langs) == 1:
                sole = next(iter(country_langs))
                lang_ids = next(
                    (ids for code, _label, ids in languages if code == sole),
                    [],
                )
                if set(lang_ids) == set(site_ids):
                    _set_language_setting(settings, sole, effective == "on")
                    lang_setting_changes.append((sole, effective == "on"))

    save_sites_config(args.sites_file, config)
    if lang_setting_changes:
        save_settings_config(args.settings_file, settings)

    # Report unique site changes in stable order
    reported = set()
    name_by_id = {r["id"]: r["name"] for r in rows}
    for sid, enabled in all_changed:
        if sid in reported:
            continue
        reported.add(sid)
        print(f"{sid} ({name_by_id.get(sid, sid)}) -> {_flag(enabled).strip()}")

    for lang, enabled in lang_setting_changes:
        label = KNOWN_LANGUAGES.get(lang, lang)
        print(f"language {lang} ({label}) -> {_flag(enabled).strip()}")

    print(f"Updated {args.sites_file}")
    if lang_setting_changes:
        print(f"Updated {args.settings_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
