# Authoring grammar recipes

Short checklist for extending the closed recipe allowlist used by site YAML grammars.

See also: [grammar-based-parsers.md](./grammar-based-parsers.md), [grammar-parsers-implementation-plan.md](./grammar-parsers-implementation-plan.md).

---

## When to add a recipe

Add a named function in `parsers/recipes.py` only when:

1. CSS selectors + title/link preference chains cannot express the behaviour, **and**
2. The behaviour is reusable (or likely to be) across more than one site, **or**
3. It is a deliberate, reviewed escape for one hard site (document which).

Do **not** add per-site Python modules. Do **not** put arbitrary expressions in YAML.

---

## Checklist (PR)

1. Implement the function in `parsers/recipes.py` and register it in `RECIPES`.
2. Name it with a stable snake_case id (`teaserlink_fluid_lines`, not `bt_fix`).
3. Keep it pure: input element and/or string → string (or score int for `score_*`).
4. Reference it from the site grammar: `recipe: name` or `recipe: [a, b, c]`.
5. Extend `parsers/schema/site.schema.json` only if new strategy fields are required (recipe *names* are validated at load via the allowlist, not the JSON Schema enum).
6. Add or update an HTML fixture under `tests/fixtures/html/<site>.html` and golden `tests/fixtures/expected/<site>.json`.
7. Run:

```bash
python -m parsers --validate
pytest tests/ -q
```

8. Note the recipe and the site(s) that need it in the PR description.

---

## Built-in recipes (current)

| Name | Role |
|------|------|
| `text` | `get_text(" ", strip=True)` |
| `aria_label` | element `aria-label` |
| `text_or_aria` | text, else aria-label |
| `teaserlink_fluid_lines` | BT/Berlingske/Weekendavisen fluid-line rebuild |
| `nested_heading` | first `h1–h5` descendant |
| `strip_dcr_quotes` | Guardian DCR a11y quotation tokens |
| `first_comma_chunk` | keep first segment when `", "` and length > 90 |
| `first_comma_chunk_100` | same with length > 100 |
| `score_by_heading_level` | h1→10 … h4→7 |

---

## Strategy types (reminder)

| Type | Use |
|------|-----|
| `card` | Container → title → link |
| `select` | Match nodes → title recipe → link |
| `link_scan` | Scan anchors; href allow/deny; title chain |
| `heading_scan` | Scan headings; resolve nearby / ancestor link |

Global text hygiene (`CandidateCollector`, noise, glue cleanup) stays in `parsers/base.py` — do not reimplement in recipes.
