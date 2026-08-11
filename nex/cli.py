"""Command-line entry: argparse and run orchestration."""

from __future__ import annotations

import argparse
import os
import sys
import time
import webbrowser

import requests

from parsers import (
    PARSERS,
    UnsupportedSiteError,
    get_parser_by_id,
    list_sites,
    refresh,
)
from parsers import PARSER_ENGINE as _DEFAULT_PARSER_ENGINE
import parsers as parsers_pkg

from .cache import (
    load_cache,
    load_results_from_cache,
    save_cache,
    split_new_vs_seen,
)
from .config import (
    discover_categories_from_hrefs,
    language_label,
    load_categories,
    load_settings_config,
    load_sites_config,
    normalize_languages,
    normalize_site_order,
    save_categories,
)
from .constants import (
    APP_REPO_URL,
    DEFAULT_CACHE_FILE,
    DEFAULT_CATEGORIES_FILE,
    DEFAULT_HTML_FILE,
    DEFAULT_LOGOS_DIR,
    DEFAULT_SETTINGS_FILE,
    DEFAULT_SITES_FILE,
)
from .console import format_site_output
from .fetch import clean_downloaded_logos, ensure_site_logo
from .pipeline import extract_from_site
from .presentation import build_combined_html
from .update_check import check_for_update


def _remove_file(path):
    if not path or not os.path.isfile(path):
        return None
    try:
        os.remove(path)
        return path.replace("\\", "/")
    except OSError as e:
        print(f"Warning: couldn't remove {path} ({e})", file=sys.stderr)
        return None


def run_clean(cache_file, html_file, logos_dir=DEFAULT_LOGOS_DIR):
    """Delete cache, generated HTML, and downloaded publisher logos."""
    removed = []
    for path in (cache_file, html_file):
        gone = _remove_file(path)
        if gone:
            removed.append(gone)
    removed.extend(clean_downloaded_logos(logos_dir))
    if removed:
        for path in removed:
            print(f"Removed {path}", file=sys.stderr)
        print(f"Cleaned {len(removed)} file(s).", file=sys.stderr)
    else:
        print("Nothing to clean.", file=sys.stderr)
    return removed

def main():
    _started = time.perf_counter()
    try:
        _main()
    finally:
        print(f"Elapsed: {time.perf_counter() - _started:.2f}s", file=sys.stderr)


def _main():
    parser = argparse.ArgumentParser(
        description="Extract headlines from enabled site parsers (cache by default; --update to fetch)."
    )
    parser.add_argument(
        "--list-sites",
        action="store_true",
        help="List discovered site parsers (and enabled flag) and exit",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove cache, generated HTML, and downloaded publisher logos, then exit "
             "(keeps NewsExtract brand assets and config JSON)",
    )
    parser.add_argument(
        "--check-update",
        action="store_true",
        help="Check remote version manifest and exit",
    )
    parser.add_argument(
        "--no-update-check",
        action="store_true",
        help="Skip automatic update check on startup",
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

    if args.clean:
        html_path = None if args.no_html else (args.html or DEFAULT_HTML_FILE)
        run_clean(args.cache_file, html_path, logos_dir=DEFAULT_LOGOS_DIR)
        return

    should_check_update = args.check_update or not args.no_update_check
    update_notice = None
    if should_check_update:
        status, message, remote_version, notes_url = check_for_update()
        if status == "update_available" or args.check_update or args.verbose:
            print(message, file=sys.stderr)
        if status == "update_available":
            update_notice = {
                "message": f"New version available: v{remote_version}",
                "url": notes_url or APP_REPO_URL,
            }
        if args.check_update:
            return

    refresh()
    parsers = list(PARSERS)
    if not parsers:
        print(
            "No site parsers found in parsers/grammar/<country>/. "
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
            )
            if logo:
                block["logo"] = logo

        site_domains = {
            mod.SITE_ID: tuple(getattr(mod, "DOMAINS", ()) or ())
            for mod in parsers
        }
        page = build_combined_html(
            site_blocks, categories, sites_config, settings, site_domains, update_notice=update_notice
        )
        out_path = args.html or DEFAULT_HTML_FILE
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(page)
        abs_path = os.path.abspath(out_path)
        webbrowser.open(f"file://{abs_path}")
        print(f"Opened {abs_path}", file=sys.stderr)
