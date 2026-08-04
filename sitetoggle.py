#!/usr/bin/env python3
"""
sitetoggle.py — turn news sites on/off in sites.json.

Usage:
    python sitetoggle.py              # interactive list + toggle
    python sitetoggle.py bt dr        # toggle those site ids
    python sitetoggle.py on bt        # enable
    python sitetoggle.py off guardian # disable
    python sitetoggle.py --list       # show status only
"""

from __future__ import annotations

import argparse
import sys

from parsers import PARSERS, refresh
from nex.config import load_sites_config, save_sites_config
from nex.constants import DEFAULT_SITES_FILE


def _flag(enabled: bool) -> str:
    return "ON " if enabled else "OFF"


def _site_rows(config: dict) -> list[tuple[str, str, bool]]:
    """Ordered (site_id, display_name, enabled) for known parsers."""
    rows = []
    for mod in PARSERS:
        sid = mod.SITE_ID
        entry = config.get(sid) if isinstance(config.get(sid), dict) else {}
        name = entry.get("name") or getattr(mod, "NAME", sid)
        enabled = bool(entry.get("enabled", True))
        rows.append((sid, name, enabled))
    return rows


def _print_table(rows: list[tuple[str, str, bool]]) -> None:
    width = max((len(sid) for sid, _, _ in rows), default=8)
    for i, (sid, name, enabled) in enumerate(rows, start=1):
        print(f"  {i:2d}. [{_flag(enabled)}]  {sid:<{width}}  {name}")


def _resolve_token(token: str, rows: list[tuple[str, str, bool]]) -> str | None:
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        n = int(token)
        if 1 <= n <= len(rows):
            return rows[n - 1][0]
        return None
    lower = token.lower()
    for sid, name, _ in rows:
        if sid.lower() == lower or name.lower() == lower:
            return sid
    # Prefix match if unique
    hits = [sid for sid, name, _ in rows if sid.lower().startswith(lower)]
    if len(hits) == 1:
        return hits[0]
    return None


def _apply(config: dict, site_id: str, mode: str) -> bool:
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Toggle site enabled flags in sites.json",
    )
    parser.add_argument(
        "--sites-file",
        default=DEFAULT_SITES_FILE,
        help=f"sites.json path (default: {DEFAULT_SITES_FILE})",
    )
    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="List sites and exit (no changes)",
    )
    parser.add_argument(
        "targets",
        nargs="*",
        help="Site ids/names/numbers, optionally starting with on|off",
    )
    args = parser.parse_args()

    refresh()
    if not PARSERS:
        print("No site parsers found in parsers/grammar/.", file=sys.stderr)
        return 1

    config = load_sites_config(args.sites_file, list(PARSERS))
    rows = _site_rows(config)

    if args.list or not args.targets:
        print(f"Sites in {args.sites_file}:")
        _print_table(rows)
        if args.list:
            return 0
        print()
        print("Enter number(s) or id(s) to toggle, comma/space separated (q to quit):")
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
            if mode == "toggle":
                mode = "toggle"

    if not tokens:
        print("No sites specified.", file=sys.stderr)
        return 1

    changed = []
    for token in tokens:
        sid = _resolve_token(token, rows)
        if not sid:
            print(f"Unknown site: {token}", file=sys.stderr)
            return 1
        new_state = _apply(config, sid, mode)
        name = next(n for s, n, _ in rows if s == sid)
        changed.append((sid, name, new_state))

    save_sites_config(args.sites_file, config)
    for sid, name, enabled in changed:
        print(f"{sid} ({name}) -> {_flag(enabled).strip()}")
    print(f"Updated {args.sites_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
