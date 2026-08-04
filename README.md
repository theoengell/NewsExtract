# NewsExtract

**NewsExtract** is a local headline dashboard for Danish and English news front pages. It fetches only the front page of each enabled site, extracts headlines, tracks what you’ve already seen, and opens a single HTML page you can filter, collapse, and personalise in the browser.

Only front pages are read. Preferences are stored in browser cookies / `localStorage` (and optionally in project JSON files when you export settings).

Created by **Theo Engell**.

---

## Features

- **Multi-site parsers** — Berlingske, BT, Børsen, DR, Ekstra Bladet, Fyens Stiftstidende, Jyllands-Posten, Newz, Politiken, Sjællandske Nyheder, Slashdot, TV 2 Nyheder, Weekendavisen, The Guardian, The New York Times, The Observer (YAML grammars under `parsers/grammar/`)
- **Grammar engine** — one interpreter for all sites; see [documentation/tech/grammar-based-parsers.md](documentation/tech/grammar-based-parsers.md)
- **Cache-first** — default run rebuilds the dashboard from `config/headlines_cache.json` with no network; use `--update` to fetch fresh pages
- **New vs previously seen** — compares against the cache so you can scan what’s changed since last update
- **HTML dashboard** (`headlines.html`) with:
  - Per-site sections (logo + title; click to collapse/expand)
  - Collapsed headers show new-article counts when there are any
  - **All new** unified feed across sites (also click to collapse/expand)
  - **Opened today** list; dim opened links
  - Category colouring from URL path patterns
  - Languages (Danish / English) on/off
  - Drag-to-reorder sites in Settings
  - Keyword **bold** highlights and **exclude** filters
  - Pagination for “Previously seen” (Previous / Next + page label)
  - Dark / light mode
  - Settings popup; **Save for next run** downloads updated `sites.json` + `settings.json` + `categories.json`
- **CLI** summary of new/seen counts per site, optional verbose lists, text export, elapsed time

---

## Requirements

- Python 3.9+ (3.10+ recommended)
- Dependencies:

```bash
pip install requests beautifulsoup4 pyyaml jsonschema
```

(On some Linux setups you may need `pip install requests beautifulsoup4 pyyaml jsonschema --break-system-packages`.)

Optional for tests: `pip install pytest`.

---

## Quick start

```bash
cd C:\source\repos\NewsExtract

# First time / refresh from the web
python newsextract.py --update

# Later: rebuild HTML from cache only (no network)
python newsextract.py
```

The script writes `headlines.html` and opens it in your default browser (unless you pass `--no-html`).

---

## Usage

| Command | What it does |
|--------|----------------|
| `python newsextract.py` | Rebuild console + HTML from cache |
| `python newsextract.py --update` | Fetch enabled sites, update cache, rebuild |
| `python newsextract.py --update -v` | Same, and print full headline lists |
| `python newsextract.py --list-sites` | List discovered parsers and enabled flags |
| `python newsextract.py --only ekstrabladet,dr` | Run only those site ids |
| `python newsextract.py --no-html` | Console only (no HTML write/open) |
| `python newsextract.py --limit 20 -o headlines.txt` | Cap list length; write titles to a file |
| `python newsextract.py --with-links` | Print title + URL (implies verbose) |
| `python newsextract.py --clean` | Remove cache, HTML, and downloaded site logos |
| `python newsextract.py --check-update` | Check for newer version on GitHub and exit |
| `python sitetoggle.py` | Interactive on/off for sites in `config/sites.json` |
| `python sitetoggle.py bt dr` | Toggle those site ids |
| `python sitetoggle.py on guardian` | Enable a site |
| `python sitetoggle.py off bt` | Disable a site |
| `python newsextract.py --parser-engine both --only tv2` | Compare Python vs grammar extract |

### Useful options

| Option | Description |
|--------|-------------|
| `--parser-engine` | `py` (default), `grammar`, or `both` (parity check; uses py output) |
| `--min-len` / `--max-len` | Headline length filter (defaults 8 / 200) |
| `--html [PATH]` | HTML output path (default `headlines.html`) |
| `--cache-file PATH` | Cache file (default `config/headlines_cache.json`) |
| `--no-cache` | Don’t read/write cache (requires `--update`) |
| `--clean` | Remove cache, generated HTML, and downloaded publisher logos (then exit) |
| `--check-update` | Check remote version manifest and exit |
| `--no-update-check` | Skip automatic update check on startup |
| `--sites-file PATH` | Site enablement (default `config/sites.json`) |
| `--settings-file PATH` | Global UI settings (default `config/settings.json`) |
| `--categories PATH` | Categories file (default `config/categories.json`) |

Every run prints `Elapsed: X.XXs` on stderr when finished.

---

## Project layout

```text
C:\source\repos\NewsExtract\
├── newsextract.py         # Thin CLI entry (`python newsextract.py`)
├── sitetoggle.py          # Toggle sites on/off in config/sites.json
├── nex/                   # App library (fetch, config, cache, pipeline, HTML)
│   ├── cli.py             # argparse + run orchestration
│   ├── fetch.py           # HTTP + site logo download
│   ├── config.py          # sites / settings / categories I/O
│   ├── cache.py           # headline cache + new/seen split
│   ├── pipeline.py        # extract → filter → dedupe → merge
│   ├── console.py         # CLI text formatting
│   ├── presentation.py    # HTML dashboard builder
│   └── constants.py       # Default paths and category seeds
├── config/
│   ├── sites.json         # Per-site enabled flags + metadata
│   ├── settings.json      # Global UI defaults (order, filters, theme, …)
│   ├── categories.json    # Category labels, colours, URL match patterns
│   └── headlines_cache.json # Seen hrefs + last display snapshot (created on --update)
├── version.json           # Local app version + release metadata
├── headlines.html         # Generated dashboard
├── logos/                 # Brand assets + downloaded site logos (site logos gitignored)
│   ├── newsextract*.png   # NewsExtract brand (tracked)
│   └── <site_id>.*        # Publisher favicons (downloaded on demand)
├── fonts/                 # Bundled Lato fonts
├── parsers/
│   ├── __init__.py        # Discovers grammar sites
│   ├── base.py            # Shared noise filters / text cleanup
│   ├── engine.py          # YAML grammar interpreter
│   ├── recipes.py         # Named title/link transforms
│   ├── schema/            # JSON Schema for grammars
│   └── grammar/           # One YAML file per site
├── tests/                 # Schema + fixture parity tests
├── documentation/tech/    # Design + implementation plan
├── LICENSE                # GPL-3.0
└── README.md
```

---

## Configuration

### `config/sites.json`

Each site key matches a grammar file stem (`parsers/grammar/<id>.yaml`):

```json
"ekstrabladet": {
  "enabled": true,
  "name": "Ekstra Bladet",
  "url": "https://ekstrabladet.dk/",
  "language": "da"
}
```

### `config/settings.json`

Global UI defaults, for example:

| Key | Purpose |
|-----|---------|
| `show_external` | Show off-site / external article links |
| `only_new` | Hide “previously seen” blocks |
| `show_all_new` | Show the combined “All new” section |
| `dim_opened` | Dim headlines you’ve opened |
| `show_opened_today` | Show the “Opened today” panel |
| `dark_mode` | Start in dark theme |
| `languages` | `{ "da": true, "en": true }` |
| `site_order` | Display order of site ids |
| `seen_limit` | Page size for “Previously seen” |
| `bg_strength` | Category colour strength (0–100) |
| `highlight_words` | Comma-separated words to bold |
| `exclude_words` | Comma-separated words that hide headlines |

Toggling sites/languages/filters in the HTML Settings panel updates the browser. **Save for next run** downloads JSON you can drop back into the project folder so the next script run picks them up.

If `config/settings.json` is missing but an older `config/sites.json` still has a `_settings` block, that block is migrated automatically on the next run.

### `config/categories.json`

Categories map URL path fragments to a label and background colour. Unmatched URL sections discovered during a run can be added automatically. Enable/disable categories in Settings (A–Z).

### `config/headlines_cache.json`

Created/updated by `--update`. Stores per-site href history and a `last_display` snapshot used for cache-only rebuilds.

---

## HTML dashboard

Open `headlines.html` after a run (or let the script open it).

### Header

- **NewsExtract** brand
- Dark / Light toggle
- **Settings**

### Per site

- Click the **logo or title** to collapse/expand (same for **All new**)
- Collapsed: right-aligned “N new articles” when there are new items
- **New since last run** and **Previously seen (x of y)**
- Previously seen pagination: `Previous X` · `(z of w)` · `Next X` (page size = Settings → previously seen limit). Short last pages are padded so height stays stable.

### Settings highlights

- Languages (hide whole language groups)
- Sites on/off + drag reorder
- Categories on/off
- Link filters (external, only new, all new, dim opened, opened today, dark mode)
- Category colour strength
- Previously seen page size
- Bold headlines containing …
- Exclude headlines containing …
- Save for next run / Clear all cookies & saved settings

Browser preferences use `localStorage` keys under the `newsextract.` prefix (site visibility, collapsed sites, opened links, highlight/exclude words, etc.).

---

## Adding a site parser

1. Create `parsers/grammar/<site_id>.yaml` (stem = site id in `config/sites.json`).
2. Define metadata and one or more strategies, for example:

```yaml
id: example
name: Example News
language: en
url: https://www.example.com/
domains:
  - example.com

strategies:
  - type: card
    container: ".teaser"
    title: "h2.title"
    link: "a.teaser-link[href]"
    link_fallback: "a[href]"
    score: 9
```

3. Prefer existing strategy types (`card`, `link_scan`, `heading_scan`, `select`) and recipes in `parsers/recipes.py`. Add a new named recipe only when CSS/filters are not enough.
4. Optional: `fetch_timeout` on the grammar for a custom fetch timeout.
5. Validate: `python -m parsers --validate` (or `python -m parsers.engine --validate`)
6. Run `python newsextract.py --list-sites` — the new site should appear.
7. Run with `--update` (and optionally `--only site_id`). Missing site logos are downloaded into `logos/` on the next HTML build (publisher icons are not committed to git).

Shared helpers live in `parsers/base.py` (noise filtering, glued-headline cleanup, TeaserLink helpers, etc.). Design notes: [documentation/tech/grammar-based-parsers.md](documentation/tech/grammar-based-parsers.md). Recipe checklist: [documentation/tech/grammar-recipes.md](documentation/tech/grammar-recipes.md).

---

## How the pipeline works

1. Discover grammars in `parsers/grammar/`
2. Load `config/sites.json` / `config/settings.json` / `config/categories.json` / cache
3. If `--update`: fetch each enabled front page → grammar `extract()` → length filter → fuzzy dedupe → merge by href
4. Compare to cache → mark new vs seen → update cache
5. Print short CLI summary (or verbose lists)
6. Ensure site logos under `logos/` (download favicon if missing) → build `headlines.html` and open it (unless `--no-html`)

Without `--update`, step 3 is skipped and the last cached display snapshot is reused.

---

## Privacy & scope

- **Only front pages** of configured sites are fetched (no article body scrape in the default flow).
- Preferences and opened-link history live in your browser (`localStorage` / cookies for this file origin).
- Clearing Settings → **Clear all cookies & saved settings** removes NewsExtract browser data for the page.
- Network access is only needed for `--update` and when a site logo is missing (favicon download into `logos/`).
- Publisher logos under `logos/<site_id>.*` are local-only (gitignored). NewsExtract brand PNGs stay in the repo.

---

## Tips

- Use a scheduled `--update` (Task Scheduler / cron) and open the HTML anytime from cache for a fast local reader.
- Keep `exclude_words` for topics you never want to see; use `highlight_words` for topics you care about.
- Collapse sites you rarely read; order favourites to the top via Settings drag-and-drop, then **Save for next run**.
- `--only` is handy when debugging a single parser.

---

## License

NewsExtract is free software licensed under the [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html) (GPL-3.0). See [`LICENSE`](LICENSE) for the full text.

Copyright © Theo Engell.

Site content belongs to the respective publishers; this tool is a personal front-page headline extractor and reader UI.
