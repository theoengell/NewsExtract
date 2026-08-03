"""
Grammar engine: load site YAML and extract headline candidates.

Site files live in parsers/grammar/*.yaml and are validated against
parsers/schema/site.schema.json when jsonschema is available.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .base import HEADLINE_CLASS_HINTS, CandidateCollector
from .recipes import get_recipe, text as recipe_text

try:
    import yaml
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "PyYAML is required for grammar parsers. Install with: pip install pyyaml"
    ) from e

GRAMMAR_DIR = Path(__file__).resolve().parent / "grammar"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema" / "site.schema.json"

_SCHEMA_CACHE = None
_COMPILED_RE_CACHE: dict[str, re.Pattern] = {}


class GrammarError(ValueError):
    """Invalid or unsupported grammar definition."""


def _compile_re(pattern: str) -> re.Pattern:
    cached = _COMPILED_RE_CACHE.get(pattern)
    if cached is None:
        cached = re.compile(pattern, re.IGNORECASE)
        _COMPILED_RE_CACHE[pattern] = cached
    return cached


def _load_schema():
    global _SCHEMA_CACHE
    if _SCHEMA_CACHE is not None:
        return _SCHEMA_CACHE
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        _SCHEMA_CACHE = json.load(f)
    return _SCHEMA_CACHE


def validate_site(data: dict, path: str | Path | None = None) -> None:
    """Validate grammar dict; raise GrammarError on failure."""
    label = str(path) if path else data.get("id", "<unknown>")
    try:
        import jsonschema
    except ImportError:
        _validate_minimal(data, label)
        return

    try:
        jsonschema.validate(instance=data, schema=_load_schema())
    except jsonschema.ValidationError as e:
        path_str = ".".join(str(p) for p in e.absolute_path) or "(root)"
        raise GrammarError(f"{label}: invalid at '{path_str}': {e.message}") from e

    _validate_recipes(data, label)


def _validate_minimal(data: dict, label: str) -> None:
    for key in ("id", "name", "url", "language", "domains", "strategies"):
        if key not in data:
            raise GrammarError(f"{label}: missing required field '{key}'")
    if not isinstance(data["strategies"], list) or not data["strategies"]:
        raise GrammarError(f"{label}: 'strategies' must be a non-empty list")
    _validate_recipes(data, label)


def _iter_recipe_names(node) -> list[str]:
    names = []
    if isinstance(node, dict):
        recipe = node.get("recipe")
        if isinstance(recipe, str):
            names.append(recipe)
        elif isinstance(recipe, list):
            names.extend(r for r in recipe if isinstance(r, str))
        for v in node.values():
            names.extend(_iter_recipe_names(v))
    elif isinstance(node, list):
        for item in node:
            names.extend(_iter_recipe_names(item))
    return names


def _validate_recipes(data: dict, label: str) -> None:
    for name in _iter_recipe_names(data.get("strategies")):
        try:
            get_recipe(name)
        except KeyError as e:
            raise GrammarError(f"{label}: {e}") from e


def load_site(path: str | Path) -> dict:
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise GrammarError(f"{path}: grammar root must be a mapping")
    if "id" not in data:
        data["id"] = path.stem
    elif data["id"] != path.stem:
        raise GrammarError(
            f"{path}: id '{data['id']}' must match filename stem '{path.stem}'"
        )
    validate_site(data, path)
    return data


def load_all(grammar_dir: str | Path | None = None) -> list[dict]:
    directory = Path(grammar_dir) if grammar_dir else GRAMMAR_DIR
    if not directory.is_dir():
        return []
    sites = []
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        sites.append(load_site(path))
    return sites


def validate_all(grammar_dir: str | Path | None = None) -> list[str]:
    """Validate every grammar file; return list of error strings (empty if ok)."""
    directory = Path(grammar_dir) if grammar_dir else GRAMMAR_DIR
    errors = []
    if not directory.is_dir():
        return [f"Grammar directory missing: {directory}"]
    for path in sorted(directory.glob("*.yaml")):
        if path.name.startswith("_"):
            continue
        try:
            load_site(path)
        except GrammarError as e:
            errors.append(str(e))
        except Exception as e:  # noqa: BLE001 — surface any load failure
            errors.append(f"{path}: {e}")
    return errors


# --- title / link resolution -------------------------------------------------

def _apply_recipe_chain(value, recipe_spec, el=None):
    if recipe_spec is None:
        return value if isinstance(value, str) else recipe_text(value)
    names = recipe_spec if isinstance(recipe_spec, list) else [recipe_spec]
    current = value
    string_recipes = {
        "first_comma_chunk",
        "first_comma_chunk_100",
        "strip_dcr_quotes",
    }
    for name in names:
        fn = get_recipe(name)
        if isinstance(current, str) and name in string_recipes:
            current = fn(current)
        elif not isinstance(current, str):
            current = fn(el if el is not None else current)
        else:
            # String already; allow recipes that accept strings or re-wrap.
            current = fn(current)
    if not isinstance(current, str):
        current = recipe_text(current) if current is not None else ""
    return current


def _resolve_title_from_spec(el, spec, default_score: float | None):
    """
    Resolve title text and score from a title spec.
    Spec forms:
      - string CSS selector (relative to el)
      - { recipe, select?, from?, score? }
      - list of preference steps (first non-empty wins)
    """
    if spec is None:
        return recipe_text(el), default_score

    if isinstance(spec, str):
        node = el.select_one(spec) if hasattr(el, "select_one") else None
        if node is None:
            return "", default_score
        return recipe_text(node), default_score

    if isinstance(spec, list):
        for step in spec:
            text_val, score = _resolve_title_from_spec(el, step, default_score)
            if text_val:
                step_score = step.get("score", score) if isinstance(step, dict) else score
                return text_val, step_score if step_score is not None else default_score
        return "", default_score

    if not isinstance(spec, dict):
        raise GrammarError(f"Unsupported title spec: {spec!r}")

    target = _resolve_from(el, spec)
    if target is None:
        return "", default_score

    select = spec.get("select")
    node = target
    if select:
        node = target.select_one(select) if hasattr(target, "select_one") else None
        if node is None and select:
            # allow comma selectors via select_one already; try find for tag lists
            pass
        if node is None:
            return "", default_score

    recipe = spec.get("recipe", "text")
    text_val = _apply_recipe_chain(node, recipe, el=node)
    score = spec.get("score", default_score)
    return text_val, score


def _resolve_from(el, spec: dict):
    origin = spec.get("from", "self")
    if origin == "self":
        return el
    if origin == "parent":
        tag = spec.get("tag")
        if tag:
            return el.find_parent(tag)
        return el.parent
    if origin == "child":
        select = spec.get("select", "a[href]")
        return el.select_one(select) if hasattr(el, "select_one") else None
    if origin == "ancestor":
        # handled in link resolution
        return el
    raise GrammarError(f"Unknown 'from' value: {origin}")


def _resolve_link(el, spec, href_must=None, href_skip=None):
    """
    Resolve an <a> element (or href string) from link spec.
    """
    if spec is None:
        if getattr(el, "name", None) == "a" and el.get("href"):
            return el
        return el.find("a", href=True) if hasattr(el, "find") else None

    if isinstance(spec, str):
        return el.select_one(spec) if hasattr(el, "select_one") else None

    if isinstance(spec, list):
        for step in spec:
            found = _resolve_link(el, step, href_must=href_must, href_skip=href_skip)
            if found is not None:
                return found
        return None

    if not isinstance(spec, dict):
        raise GrammarError(f"Unsupported link spec: {spec!r}")

    origin = spec.get("from", "self")
    must = spec.get("href_must", href_must)
    skip = spec.get("href_skip", href_skip)

    if origin == "self":
        if getattr(el, "name", None) == "a" and el.get("href"):
            if _href_ok(el.get("href") or "", must, skip, None):
                return el
        return None

    if origin == "child":
        select = spec.get("select", "a[href]")
        for a in el.select(select) if hasattr(el, "select") else []:
            if a.get("href") and _href_ok(a.get("href") or "", must, skip, None):
                return a
        a = el.find("a", href=True)
        if a and _href_ok(a.get("href") or "", must, skip, None):
            return a
        return None

    if origin == "parent":
        a = el.find_parent("a", href=True)
        if a and _href_ok(a.get("href") or "", must, skip, None):
            return a
        return None

    if origin == "ancestor":
        max_depth = int(spec.get("max_depth", 8))
        node = el
        for _ in range(max_depth):
            if node is None:
                return None
            a = node.find("a", href=True) if hasattr(node, "find") else None
            if a:
                href = a.get("href") or ""
                if _href_ok(href, must, skip, None):
                    return a
            node = getattr(node, "parent", None)
        return None

    raise GrammarError(f"Unknown link 'from' value: {origin}")


def _href_ok(href: str, must, skip, skip_endswith) -> bool:
    if must:
        if not any(_compile_re(p).search(href) for p in must):
            return False
    if skip:
        if any(_compile_re(p).search(href) for p in skip):
            return False
    if skip_endswith:
        stripped = href.rstrip("/")
        for suffix in skip_endswith:
            if stripped.endswith(suffix.rstrip("/")):
                return False
    return True


def _text_filters_ok(text_val: str, strategy: dict) -> bool:
    min_len = strategy.get("min_length")
    if min_len is not None and len(text_val) < int(min_len):
        return False
    max_len = strategy.get("max_length")
    if max_len is not None and len(text_val) > int(max_len):
        return False
    low = text_val.lower()
    for prefix in strategy.get("text_drop_prefix") or []:
        if low.startswith(prefix.lower()):
            return False
    for pattern in strategy.get("text_drop_regex") or []:
        if _compile_re(pattern).search(text_val):
            return False
    return True


def _strategy_href_filters(strategy: dict):
    return (
        strategy.get("href_must"),
        strategy.get("href_skip"),
        strategy.get("href_skip_endswith"),
    )


# --- strategies --------------------------------------------------------------

def _run_card(soup, strategy: dict, collector: CandidateCollector) -> None:
    container_sel = strategy.get("container")
    if not container_sel:
        raise GrammarError("card strategy requires 'container'")
    title_spec = strategy.get("title")
    link_sel = strategy.get("link")
    link_fallback = strategy.get("link_fallback")
    default_score = strategy.get("score", 9)
    must, skip, skip_end = _strategy_href_filters(strategy)

    for card in soup.select(container_sel):
        text_val, score = _resolve_title_from_spec(card, title_spec, default_score)
        if not text_val or not _text_filters_ok(text_val, strategy):
            continue

        link = None
        if link_sel:
            link = card.select_one(link_sel)
        if link is None and link_fallback:
            link = card.select_one(link_fallback) if link_fallback != "a[href]" else None
            if link is None:
                link = card.find("a", href=True)
        if link is None and isinstance(strategy.get("link"), (dict, list)):
            link = _resolve_link(card, strategy.get("link"), href_must=must, href_skip=skip)
        if link is None:
            continue
        href = link.get("href") or ""
        if not _href_ok(href, must, skip, skip_end):
            continue
        collector.add(text_val, href, score if score is not None else default_score)


def _run_select(soup, strategy: dict, collector: CandidateCollector) -> None:
    match = strategy.get("match")
    if not match:
        raise GrammarError("select strategy requires 'match'")
    default_score = strategy.get("score", 9)
    must, skip, skip_end = _strategy_href_filters(strategy)
    title_spec = strategy.get("title", {"recipe": "text"})
    link_spec = strategy.get("link", {"from": "self"})

    for el in soup.select(match):
        text_val, score = _resolve_title_from_spec(el, title_spec, default_score)
        if not text_val or not _text_filters_ok(text_val, strategy):
            continue
        link = _resolve_link(el, link_spec, href_must=must, href_skip=skip)
        href = None
        if link is not None:
            href = link.get("href")
        elif getattr(el, "name", None) == "a":
            href = el.get("href")
        if href is None:
            continue
        if not _href_ok(href, must, skip, skip_end):
            continue
        collector.add(text_val, href, score if score is not None else default_score)


def _run_link_scan(soup, strategy: dict, collector: CandidateCollector) -> None:
    match = strategy.get("match")
    default_score = strategy.get("score", 9)
    must, skip, skip_end = _strategy_href_filters(strategy)
    title_spec = strategy.get("title", {"recipe": "text"})
    class_hint = bool(strategy.get("class_hint"))

    if match:
        anchors = soup.select(match)
    else:
        anchors = soup.find_all("a", href=True)

    for a in anchors:
        href = a.get("href") or ""
        if not href:
            continue
        if not _href_ok(href, must, skip, skip_end):
            continue

        if class_hint:
            classes = " ".join(a.get("class", []) or []) + " " + (a.get("id") or "")
            text_val = recipe_text(a)
            soft_min = int(strategy.get("soft_min_length", strategy.get("min_length", 25)))
            if HEADLINE_CLASS_HINTS.search(classes):
                score = strategy.get("score", 6)
            elif len(text_val) >= soft_min:
                score = strategy.get("soft_score", 3)
            else:
                continue
            # Class-hint path does not apply min_length (matches ekstrabladet.py).
            filter_strategy = {
                k: v for k, v in strategy.items() if k not in ("min_length", "soft_min_length")
            }
            if not _text_filters_ok(text_val, filter_strategy):
                continue
            collector.add(text_val, href, score)
            continue

        text_val, score = _resolve_title_from_spec(a, title_spec, default_score)
        if not text_val or not _text_filters_ok(text_val, strategy):
            continue
        collector.add(text_val, href, score if score is not None else default_score)


def _run_heading_scan(soup, strategy: dict, collector: CandidateCollector) -> None:
    match = strategy.get("match")
    tags = strategy.get("tags") or ["h1", "h2", "h3", "h4"]
    default_score = strategy.get("score", 9)
    must, skip, skip_end = _strategy_href_filters(strategy)
    title_spec = strategy.get("title", {"recipe": "text"})
    link_spec = strategy.get("link")
    score_recipe = strategy.get("score_recipe")

    if match:
        headings = soup.select(match)
    else:
        headings = soup.find_all(tags)

    for h in headings:
        text_val, score = _resolve_title_from_spec(h, title_spec, default_score)
        if score_recipe:
            score = get_recipe(score_recipe)(h)
        if not text_val or not _text_filters_ok(text_val, strategy):
            continue

        link = None
        if link_spec is not None:
            link = _resolve_link(h, link_spec, href_must=must, href_skip=skip)
        else:
            link = h.find("a", href=True) or h.find_parent("a", href=True)

        href = link.get("href") if link is not None else None
        if href is not None and not _href_ok(href, must, skip, skip_end):
            continue
        # Observer/Guardian require a link; Ekstra Bladet headings allow missing href
        if strategy.get("require_link", True) and not href:
            continue
        collector.add(
            text_val,
            href,
            score if score is not None else default_score,
        )


def _decode_data_params(raw: str) -> dict | None:
    import base64
    import json

    if not raw:
        return None
    pad = "=" * (-len(raw) % 4)
    try:
        return json.loads(base64.b64decode(raw + pad))
    except Exception:
        return None


def _run_cdp_priority(soup, strategy: dict, collector: CandidateCollector) -> None:
    """
    Sjællandske Nyheder-style front pages: article teasers are loaded via
    Aptoma/CDP ``{apiURL}/priority/?imgPack=landing&query=<base64 json>``.
    Query blocks are embedded on the page as base64 ``data-params``.
    """
    import base64
    import json

    import requests

    default_score = strategy.get("score", 9)
    title_field = strategy.get("title_field", "headline")
    path_field = strategy.get("path_field", "path")
    slug_field = strategy.get("slug_field", "slug")
    img_pack = strategy.get("img_pack", "landing")
    timeout = float(strategy.get("timeout") or 15)

    seen_queries: set[str] = set()
    seen_ids: set = set()

    for el in soup.select("[data-params]"):
        block = _decode_data_params(el.get("data-params") or "")
        if not block:
            continue
        api = (block.get("apiURL") or "").rstrip("/")
        query = block.get("query")
        if not api or not isinstance(query, dict):
            continue
        qkey = json.dumps(query, sort_keys=True, separators=(",", ":"))
        if qkey in seen_queries:
            continue
        seen_queries.add(qkey)

        endpoint = f"{api}/priority/"
        q_b64 = base64.b64encode(
            json.dumps(query, separators=(",", ":")).encode("utf-8")
        ).decode("ascii")
        try:
            resp = requests.get(
                endpoint,
                params={"imgPack": img_pack, "query": q_b64},
                timeout=timeout,
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Origin": collector.base_url.rstrip("/"),
                    "Referer": collector.base_url,
                    "User-Agent": "Mozilla/5.0 NewsExtract/1.0",
                },
            )
            resp.raise_for_status()
            payload = resp.json()
        except Exception:
            continue

        items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue

        for item in items:
            if not isinstance(item, dict):
                continue
            item_id = item.get("id")
            if item_id is not None:
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

            text_val = (item.get(title_field) or item.get("headline") or "").strip()
            if not text_val or not _text_filters_ok(text_val, strategy):
                continue

            path = (item.get(path_field) or "").strip().strip("/")
            slug = (item.get(slug_field) or "").strip().strip("/")
            href = f"/{path}/" if path else (f"/{slug}/" if slug else None)
            if not href:
                continue
            collector.add(text_val, href, default_score)


_STRATEGY_RUNNERS = {
    "card": _run_card,
    "select": _run_select,
    "link_scan": _run_link_scan,
    "heading_scan": _run_heading_scan,
    "cdp_priority": _run_cdp_priority,
}


def extract(site: dict, soup, base_url: str):
    """Run all strategies for a loaded site grammar; return candidate tuples."""
    collector = CandidateCollector(base_url)
    for i, strategy in enumerate(site.get("strategies") or []):
        stype = strategy.get("type")
        runner = _STRATEGY_RUNNERS.get(stype)
        if runner is None:
            raise GrammarError(
                f"{site.get('id', '<site>')}.strategies[{i}]: unknown type '{stype}'"
            )
        runner(soup, strategy, collector)
    return collector.results()


class GrammarSite:
    """Module-like facade so extract_headlines can treat grammars like parsers."""

    def __init__(self, data: dict):
        self._data = data
        self.SITE_ID = data["id"]
        self.NAME = data["name"]
        self.DEFAULT_URL = data["url"]
        self.DOMAINS = tuple(data.get("domains") or ())
        self.LANGUAGE = str(data.get("language") or "da").strip().lower() or "da"
        self.FETCH_TIMEOUT = int(data.get("fetch_timeout") or 15)

    def extract(self, soup, base_url: str):
        return extract(self._data, soup, base_url)


def discover_grammar_sites(grammar_dir: str | Path | None = None) -> list[GrammarSite]:
    return [GrammarSite(data) for data in load_all(grammar_dir)]


def get_grammar_by_id(site_id: str, grammar_dir: str | Path | None = None) -> GrammarSite | None:
    for site in discover_grammar_sites(grammar_dir):
        if site.SITE_ID == site_id:
            return site
    return None


def compare_candidates(py_cands, grammar_cands):
    """
    Compare candidate lists for --parser-engine=both.
    Returns dict with only_py, only_grammar, score_mismatch counts and samples.
    """
    from .base import normalize_for_dedup

    def key(c):
        text, score, href, _pos = c[0], c[1], c[2], c[3] if len(c) > 3 else None
        return (normalize_for_dedup(text), href or "")

    py_map = {}
    for c in py_cands:
        py_map[key(c)] = c
    gr_map = {}
    for c in grammar_cands:
        gr_map[key(c)] = c

    only_py = [py_map[k] for k in py_map.keys() - gr_map.keys()]
    only_gr = [gr_map[k] for k in gr_map.keys() - py_map.keys()]
    score_mismatch = []
    for k in py_map.keys() & gr_map.keys():
        if py_map[k][1] != gr_map[k][1]:
            score_mismatch.append((py_map[k], gr_map[k]))

    return {
        "only_py": only_py,
        "only_grammar": only_gr,
        "score_mismatch": score_mismatch,
        "ok": not only_py and not only_gr and not score_mismatch,
    }


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(
            "Usage: python -m parsers --validate\n"
            "       python -m parsers.engine --validate",
            file=sys.stderr,
        )
        return 2
    if argv[0] == "--validate":
        errors = validate_all()
        if errors:
            for err in errors:
                print(err, file=sys.stderr)
            return 1
        n = len(list(GRAMMAR_DIR.glob("*.yaml"))) if GRAMMAR_DIR.is_dir() else 0
        print(f"OK: {n} grammar file(s) validated")
        return 0
    print(f"Unknown command: {argv[0]}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
