#!/usr/bin/env python3
"""
newsextract.py

NewsExtract CLI entry point. Implementation lives in the ``nex`` package.

Discovers site parsers in the parsers/ folder. By default rebuilds the HTML
dashboard from the local cache (no network). Pass --update to fetch each
enabled site's front page and refresh the cache.

No URL arguments needed - add a grammar under parsers/grammar/. Toggle sites in
sites.json, UI defaults in settings.json, and categories in categories.json
(or on the HTML page). Unmatched URL sections are auto-added to categories.json
on each run.

Usage:
    python newsextract.py
    python newsextract.py --check-update
    python newsextract.py --update
    python newsextract.py --update -v
    python newsextract.py --list-sites
    python newsextract.py --no-update-check
    python newsextract.py --limit 20 -o headlines.txt
    python newsextract.py --no-html
    python newsextract.py --clean
    python newsextract.py --only ekstrabladet,dr

Install dependencies first:
    pip install requests beautifulsoup4 pyyaml jsonschema
"""

from nex.cli import main

if __name__ == "__main__":
    main()
