# Grammar-based site parsers

Investigation into replacing imperative per-site Python modules (`parsers/*.py`) with a shared declarative grammar evaluated by one engine.

**Implementation plan:** [grammar-parsers-implementation-plan.md](./grammar-parsers-implementation-plan.md)  
**Recipe authoring:** [grammar-recipes.md](./grammar-recipes.md)

---

## 1. Problem

Today each news site is a Python module that:

1. Declares metadata (`NAME`, `DEFAULT_URL`, `DOMAINS`, `LANGUAGE`)
2. Implements `extract(soup, base_url) → [(text, score, href, pos)]`
3. Walks the DOM with BeautifulSoup / CSS selectors and site-specific heuristics

Shared behaviour already lives in `parsers/base.py` (`CandidateCollector`, noise filters, glued-headline cleanup, TeaserLink fluid-line rebuild). Site modules still duplicate structure: card loops, href allow/deny regexes, title preference chains, multi-pass scoring.

| Pain | Why it matters |
|------|----------------|
| New site = new code | Requires a deployable Python change and code review for selector tweaks |
| Inconsistent patterns | Ten parsers reinvent “find heading near link” slightly differently |
| Hard to audit | Rules are buried in control flow, not data |
| Security / sandboxing | `importlib` loads and runs arbitrary module code |

**Goal:** describe *what* to extract in a grammar (data), and run *how* once in a shared interpreter.

---

## 2. Current landscape (what we actually extract)

All ten parsers share one contract and feed `CandidateCollector`. They cluster into a few structural families:

| Family | Sites | Shape |
|--------|-------|-------|
| **Card** | TV2, DR | Container → title selector → link selector → fixed score |
| **TeaserLink** | BT, Berlingske, Weekendavisen | Link with CSS module class + named title recipe (`teaserlink_headline`) |
| **Link scan** | Politiken, NYT, Guardian (pass 1), Observer | Scan `<a>` / headings; gate on href regex; skip paths; prefer nested heading |
| **Heading + nearest article link** | Guardian (pass 2), Observer | Start from headline node; resolve link via child/parent/ancestor walk |
| **Heuristic scan** | Ekstra Bladet | All `h1–h4` + all `a`; score by tag level / class hint / length |

None use JSON-LD or Open Graph today. Post-processing that should stay global (not per-site grammar):

- `clean_glued_headline`, `collapse_repeated_phrase`, `is_probably_noise`
- Dedup by normalized text, best score / earliest position
- Pipeline stages after extract: length filter, fuzzy dedupe, merge by href

---

## 3. External approaches (for context)

Declarative scraping is a well-trodden path. Useful references, not recommendations to adopt wholesale:

| Approach | Idea | Fit for NewsExtract |
|----------|------|---------------------|
| YAML/JSON site configs (Scrapit, scraperkit, topscrape) | Fields = CSS/XPath + transforms | Good model; too product-oriented (pagination, storage) for our front-page use case |
| Schema codegen (ssc-gen / KDL) | Grammar → generated Python | Adds build step; we want one interpreter, not generated modules |
| Scraplet-style step pipelines | URL trigger + ordered steps, no arbitrary code | Strong safety story; our “steps” are DOM strategies, not fetch pipelines |
| Scrapy Item Loaders | Declarative field processors | Overweight for “list of teasers from one HTML blob” |

**Takeaway:** keep a *small, domain-specific grammar* aimed at front-page headline cards, interpreted in-process against BeautifulSoup (or a CSS engine). Do not pull a general scrape framework.

---

## 4. Proposed design

### 4.1 Split: grammar vs engine vs recipes

```
parsers/
  grammar/                 # data only — no extract() code
    tv2.yaml
    bt.yaml
    …
  engine.py                # loads grammar, runs strategies, returns candidates
  recipes.py               # named title/link transforms (Python, shared, versioned)
  base.py                  # CandidateCollector + global text hygiene (unchanged role)
  __init__.py              # discover YAML (or .json) instead of .py modules
```

- **Grammar** — site metadata + ordered list of *strategies* (pure data).
- **Engine** — interprets strategies; always ends in `CandidateCollector`.
- **Recipes** — small, named, reviewed Python helpers for DOM walks that CSS cannot express (TeaserLink fluid lines, “nearest article ancestor link”, etc.).

Site authors edit grammar files. Recipe additions are rare and code-reviewed.

### 4.2 Why “grammar” rather than “config”

Calling it a grammar stresses:

1. A **closed vocabulary** of strategy kinds and recipe names (validated on load).
2. **Composition** — multiple strategies per site, ordered, with scores (like productions).
3. **Deterministic evaluation** — same HTML → same candidates; no `eval` / dynamic imports of site code.

Format: **YAML** (readable, comments) or **JSON** (stricter, no comments). Recommend YAML with a JSON Schema for validation in CI / `--list-sites`.

---

## 5. Grammar sketch

### 5.1 Top-level site document

```yaml
id: tv2                          # must match filename stem / sites.json key
name: TV 2 Nyheder
language: da
url: https://nyheder.tv2.dk/
domains: [nyheder.tv2.dk, tv2.dk]
# optional:
# fetch_timeout: 15

strategies:
  - type: card
    container: ".tc_teaser"
    title: "h2.tc_heading, h3.tc_heading, h4.tc_heading, .tc_heading"
    link: "a.tc_teaser__link[href]"
    link_fallback: "a[href]"       # first match in container
    score: 9
```

Equivalent today’s `parsers/tv2.py` — pure selectors, no Python.

### 5.2 Strategy types (closed set)

| `type` | Meaning | Covers |
|--------|---------|--------|
| `card` | For each `container`, resolve title + link inside it | TV2, DR |
| `link_scan` | For each `a[href]` matching filters, resolve title | Politiken, NYT, Guardian pass 1 |
| `heading_scan` | For each heading matching selector, resolve link | Observer, Guardian pass 2, Ekstra Bladet headings |
| `select` | For each node matching a selector, apply title/link recipes | TeaserLink family, generic escape |

Sites may list **multiple strategies**; all feed one `CandidateCollector` (same multi-pass behaviour as DR / Guardian today).

### 5.3 Shared filters (attachable to any strategy)

```yaml
href_must: ["/art\\d+"]              # any regex must match
href_skip: ["/shop/", "/tag/"]       # any regex → drop
href_skip_endswith: ["/video"]       # path convenience
min_length: 12
max_length: null                     # optional; pipeline also filters
text_drop_prefix: ["sign up", "tell us:"]
text_drop_regex: ["(?i)photo.?credit"]
```

### 5.4 Title resolution (preference chain)

Instead of nested `if/else` in Python:

```yaml
title:
  - { from: self, select: "h1, h2, h3, h4, h5", score: 9 }
  - { from: parent, tag: article, select: "h1, h2, h3, h4, h5", score: 8 }
  - { from: self, recipe: text, score: 5 }
```

Semantics: try in order; first non-empty title wins (and its score). Maps directly to Politiken’s current logic.

Named **`recipe`** values (engine looks up in `recipes.py`):

| Recipe | Behaviour |
|--------|-----------|
| `text` | `get_text(" ", strip=True)` |
| `aria_label` | element `aria-label` |
| `teaserlink_fluid_lines` | current `teaserlink_headline` |
| `nested_heading` | first `h1–h5` descendant |
| `first_comma_chunk` | if long text contains `", "`, keep first segment (Guardian) |
| `strip_dcr_quotes` | strip “double/single quotation mark” a11y tokens |
| `score_by_heading_level` | `10 - (hN level - 1)` for Ekstra Bladet |

Recipes may be **chained**: `recipe: [strip_dcr_quotes, first_comma_chunk, text]`.

### 5.5 Link resolution

```yaml
link:
  - { from: self }                              # node is the <a>
  - { from: child, select: "a[href]" }
  - { from: parent, select: "a[href]" }
  - { from: ancestor, max_depth: 8, href_must: ["…"], href_skip: ["…"] }
```

The last form replaces Guardian’s `_article_link_from` without site-specific Python.

### 5.6 Worked examples

**BT (TeaserLink + skips):**

```yaml
id: bt
name: BT
language: da
url: https://www.bt.dk/
domains: [bt.dk]

strategies:
  - type: select
    match: "a[class*='TeaserLink_link'][href]"
    title: { recipe: teaserlink_fluid_lines }
    link: { from: self }
    href_skip: ["/video/reels/"]
    href_skip_endswith: ["/video"]
    score: 9
```

**Politiken (link scan + title chain):**

```yaml
id: politiken
name: Politiken
language: da
url: https://politiken.dk/
domains: [politiken.dk]

strategies:
  - type: link_scan
    href_must: ["/art\\d+"]
    href_skip: ["/om_politiken/", "/shop/", "/tag/"]
    min_length: 12
    title:
      - { from: self, select: "h1, h2, h3, h4, h5", score: 9 }
      - { from: parent, tag: article, select: "h1, h2, h3, h4, h5", score: 8 }
      - { from: self, recipe: text, score: 5 }
```

**Guardian (two strategies):**

```yaml
strategies:
  - type: link_scan
    match: 'a[data-link-name*="article"]'
    href_must: ["/20\\d{2}/[a-z]{3}/\\d{2}/", "/live/20\\d{2}/", "/ng-interactive/20\\d{2}/"]
    href_skip: ["/info/", "/help/", "/email/", "/preference/", "/crosswords/", "/games/",
                "/tone/advertisement-features", "/sign-up", "/newsletter"]
    min_length: 20
    text_drop_prefix: ["sign up", "tell us:", "support the guardian"]
    title:
      recipe: [strip_dcr_quotes, first_comma_chunk, text]
      # aria-label fallback if text empty — engine default for links
    score: 9

  - type: heading_scan
    match: ".card-headline, h3.card-headline, h4.card-headline"
    min_length: 20
    title:
      recipe: [strip_dcr_quotes, first_comma_chunk, text]
    link:
      - { from: ancestor, max_depth: 8, href_must: ["…same…"], href_skip: ["…same…"] }
    score: 7
```

**Ekstra Bladet** stays expressible as `heading_scan` (score_by_heading_level) plus a soft `link_scan` with `class_hint` / `min_length` — still declarative, but acknowledge higher false-positive risk than card sites.

---

## 6. Engine behaviour

### 6.1 Load & validate

1. Discover `parsers/grammar/*.yaml` (ignore `_*.yaml`).
2. Validate against JSON Schema (`id`, `name`, `url`, `strategies[]` with known `type` / `recipe` enums).
3. Build the same registry as today: `SITE_ID`, `DOMAINS`, `LANGUAGE`, `DEFAULT_URL`.
4. Expose a module-like facade so `extract_headlines.py` barely changes:

```python
candidates = engine.extract(site_id, soup, base_url)
# or: site = get_parser_by_id(id); site.extract(soup, url)
```

### 6.2 Evaluate

For each strategy in order:

1. Select nodes (`container` / `match` / all `a` / all headings).
2. Apply href filters.
3. Resolve title via preference chain / recipes.
4. Resolve link via preference chain.
5. Apply text filters (`min_length`, drop prefixes/regexes).
6. `collector.add(text, href, score)`.

Return `collector.results()` — identical shape to today, so length filter / fuzzy dedupe / HTML UI need no changes.

### 6.3 Safety properties

- No `eval`, no per-site `importlib` of user logic.
- Selectors are CSS strings; regexes are compiled with size limits.
- Recipe names are an allowlist in code.
- Invalid grammar fails at load with a clear site id + field path (not at fetch time mid-run).

---

## 7. What stays in Python (intentionally)

| Concern | Location | Reason |
|---------|----------|--------|
| Noise / glue / dedup | `base.py` | Universal; not site rules |
| Recipe implementations | `recipes.py` | DOM walks CSS cannot express |
| Fetch / cache / HTML build | `extract_headlines.py` | Outside parsing |
| Escape hatch (optional) | `parsers/hooks/<id>.py` | Last resort for one-off sites |

**Recommended policy:** prefer extending the grammar or adding a *named* recipe over a site hook. Hooks should be temporary and rare (target: zero for the current ten sites once Guardian ancestor walk and TeaserLink are recipes).

---

## 8. Migration plan

### Phase 0 — Schema & engine skeleton

- Define JSON Schema + empty engine that runs `card` only.
- Keep Python parsers as source of truth; engine unused in production.

### Phase 1 — Easy sites

Port **TV2**, **DR**, **Observer** to grammar; dual-run tests: `assert grammar_results == py_results` on cached HTML fixtures.

### Phase 2 — Medium sites

Port **Politiken**, **NYT**, TeaserLink trio (**BT**, **Berlingske**, **Weekendavisen**) once `teaserlink_fluid_lines` is a recipe.

### Phase 3 — Hard sites

Port **Guardian** (ancestor link + DCR recipes) and **Ekstra Bladet** (heuristic modes).

### Phase 4 — Cut over

- Discovery switches to grammar files.
- Delete `parsers/*.py` site modules (keep `base.py`, `engine.py`, `recipes.py`).
- Update README “Adding a site” to edit YAML + optional recipe PR.
- `sites.json` sync unchanged (still keyed by site id).

### Test strategy

Store one HTML fixture per site under `tests/fixtures/html/<id>.html` (or reuse cache files). Golden expected candidate lists (or hashes of sorted `(text, href, score)`). CI: grammar extract ≡ fixture expected.

---

## 9. Alternatives considered

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| **A. Custom grammar + engine (this doc)** | Fits our candidate model; no new deps; safe | Must maintain interpreter | **Preferred** |
| **B. Keep Python, share more helpers** | Minimal change | Still runs site code; weak audit story | Interim only |
| **C. Adopt Scrapit / topscrape / etc.** | Mature YAML story | Wrong abstraction (pages/fields vs teaser lists); dependency weight | Reject |
| **D. Codegen from KDL/YAML → .py** | Typed output | Build step; still “code” on disk; harder hot-edit | Reject for now |
| **E. LLM-at-runtime extraction** | Handles layout drift | Non-deterministic, costly, offline-hostile | Out of scope |

---

## 10. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| Grammar too weak for a new site | Add a recipe (code) or temporary hook; expand strategy types carefully |
| Selector drift still breaks parses | Same as today; optional later: fixture regression + “zero candidates” warning |
| YAML complexity grows into a programming language | Keep closed enums; reject arbitrary expressions; document recipes as the extension point |
| Dual system during migration | Fixture parity tests; feature flag `--parser-engine=py\|grammar` |
| Authors write bad regexes | Schema examples; compile-at-load; document `href_must` / `href_skip` patterns |

---

## 11. Recommendation

1. **Introduce a declarative site grammar** (YAML + JSON Schema) with strategy types `card`, `link_scan`, `heading_scan`, `select`.
2. **Interpret it with one engine** that always uses `CandidateCollector` and global text hygiene.
3. **Encode non-CSS behaviour as named recipes** (`teaserlink_fluid_lines`, ancestor link walk, DCR cleanup) — shared Python, not per-site scripts.
4. **Migrate sites in ease order** with HTML fixture parity tests before deleting imperative modules.
5. **Do not** adopt a general scraping framework; the problem domain is narrow (front-page headline candidates) and already has a strong shared collector model.

Rough effort: engine + schema ~1–2 days; easy sites ~0.5 day; full parity for all ten including Guardian/EB ~2–4 days with fixtures.

---

## 12. Open questions

- YAML vs JSON for grammar files (lean YAML + schema).
- Whether `sites.json` metadata (`name`, `url`, `language`) should be *generated from* grammar (single source of truth) or remain overlapping.
- Whether category / section scoping ever belongs in the grammar (not needed today).
- Optional future: `match` support for XPath where CSS is insufficient — only if recipes proliferate for structural reasons alone.
