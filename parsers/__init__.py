"""
Site parser registry with folder auto-discovery.

Python modules under ``parsers/<name>.py`` define:

  NAME         - human-readable site name
  DEFAULT_URL  - front-page URL to fetch
  DOMAINS      - optional tuple of hostnames (without leading www.)
  extract(soup, base_url) -> list[(text, score, href, pos)]

Grammar sites under ``parsers/grammar/<name>.yaml`` are also discovered.
``PARSER_ENGINE`` (``py`` | ``grammar`` | ``both``) controls which extract
implementation is used; see ``extract_for_site``.
"""

from __future__ import annotations

import importlib
import pkgutil
from urllib.parse import urlparse

from .base import normalize_domain

# Default until extract_headlines sets it from CLI.
PARSER_ENGINE = "py"


class UnsupportedSiteError(ValueError):
    """Raised when no parser is registered for a URL's domain."""


def _is_parser_module(mod) -> bool:
    return (
        hasattr(mod, "extract")
        and callable(mod.extract)
        and hasattr(mod, "NAME")
        and hasattr(mod, "DEFAULT_URL")
    )


def discover_python_parsers():
    """Import every imperative parser module under this package."""
    package = importlib.import_module(__name__)
    found = []
    skip = {"base", "engine", "recipes"}
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_") or info.name in skip:
            continue
        # Skip packages (grammar/, schema/)
        if info.ispkg:
            continue
        mod = importlib.import_module(f".{info.name}", __name__)
        if not _is_parser_module(mod):
            continue
        mod.SITE_ID = info.name
        if not hasattr(mod, "DOMAINS"):
            host = normalize_domain(urlparse(mod.DEFAULT_URL).netloc)
            mod.DOMAINS = (host,) if host else ()
        if not hasattr(mod, "LANGUAGE"):
            mod.LANGUAGE = "da"
        else:
            mod.LANGUAGE = str(mod.LANGUAGE or "da").strip().lower() or "da"
        found.append(mod)
    found.sort(key=lambda m: m.SITE_ID)
    return found


def discover_parsers():
    """
    Import every parser module under this package.
    Returns a list of modules sorted by SITE_ID (module name).
    """
    return discover_python_parsers()


def _build_registry():
    from .engine import discover_grammar_sites

    py_modules = discover_python_parsers()
    grammar_sites = discover_grammar_sites()
    grammar_by_id = {g.SITE_ID: g for g in grammar_sites}

    registry = {}
    for mod in py_modules:
        for domain in getattr(mod, "DOMAINS", ()):
            registry[normalize_domain(domain)] = mod

    # Grammar-only sites (no Python module) still register by domain.
    py_ids = {m.SITE_ID for m in py_modules}
    for g in grammar_sites:
        if g.SITE_ID in py_ids:
            continue
        for domain in g.DOMAINS:
            registry[normalize_domain(domain)] = g

    return py_modules, registry, grammar_by_id


# Populated at import time; call refresh() after adding a parser in the same process.
PARSERS, REGISTRY, GRAMMAR_BY_ID = _build_registry()


def refresh():
    """Re-scan the parsers/ folder (useful in tests / long-running processes)."""
    global PARSERS, REGISTRY, GRAMMAR_BY_ID
    PARSERS, REGISTRY, GRAMMAR_BY_ID = _build_registry()
    return PARSERS


def list_sites():
    """Return [(site_id, display_name, default_url), ...] sorted by site_id."""
    return [
        (m.SITE_ID, m.NAME, m.DEFAULT_URL)
        for m in PARSERS
    ]


def get_parser_by_id(site_id: str):
    for mod in PARSERS:
        if mod.SITE_ID == site_id:
            return mod
    # Grammar-only fallback
    g = GRAMMAR_BY_ID.get(site_id)
    if g is not None:
        return g
    known = ", ".join(m.SITE_ID for m in PARSERS) or "(none)"
    raise UnsupportedSiteError(
        f"No parser with id '{site_id}'. Known: {known}"
    )


def get_parser(url: str):
    """
    Resolve a site parser module for `url`.
    Raises UnsupportedSiteError with a helpful message if unknown.
    """
    domain = normalize_domain(urlparse(url).netloc)
    if not domain:
        raise UnsupportedSiteError(f"Could not parse a hostname from URL: {url}")

    mod = REGISTRY.get(domain)
    if mod is not None:
        return mod

    parts = domain.split(".")
    for i in range(1, len(parts) - 1):
        parent = ".".join(parts[i:])
        mod = REGISTRY.get(parent)
        if mod is not None:
            return mod

    known = ", ".join(m.SITE_ID for m in PARSERS) or "(none)"
    raise UnsupportedSiteError(
        f"No parser registered for '{domain}'. Supported sites: {known}"
    )


def extract_for_site(mod, soup, base_url: str, engine: str | None = None):
    """
    Run extract using the selected engine.

    Returns (candidates, diff_info_or_None).
    For ``both``, uses Python candidates for the pipeline and returns a compare dict.
    """
    from .engine import compare_candidates, get_grammar_by_id

    mode = (engine or PARSER_ENGINE or "py").strip().lower()
    site_id = getattr(mod, "SITE_ID", None)
    grammar = get_grammar_by_id(site_id) if site_id else None

    if mode == "grammar":
        if grammar is None:
            raise UnsupportedSiteError(
                f"No grammar for '{site_id}'. Add parsers/grammar/{site_id}.yaml"
            )
        return grammar.extract(soup, base_url), None

    if mode == "both":
        py_cands = mod.extract(soup, base_url)
        if grammar is None:
            return py_cands, {
                "ok": False,
                "only_py": [],
                "only_grammar": [],
                "score_mismatch": [],
                "missing_grammar": True,
            }
        gr_cands = grammar.extract(soup, base_url)
        diff = compare_candidates(py_cands, gr_cands)
        return py_cands, diff

    # py (default)
    return mod.extract(soup, base_url), None
