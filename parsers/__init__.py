"""
Site parser registry with grammar auto-discovery.

Site rules live in ``parsers/grammar/<id>.yaml``. The shared engine in
``parsers/engine.py`` interprets them. Shared DOM recipes live in
``parsers/recipes.py``.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .base import normalize_domain
from .engine import GrammarSite, discover_grammar_sites, get_grammar_by_id

# Default extract engine. ``py`` / ``both`` remain for debugging if legacy
# modules are reintroduced; production uses ``grammar``.
PARSER_ENGINE = "grammar"


class UnsupportedSiteError(ValueError):
    """Raised when no parser is registered for a URL's domain."""


def discover_parsers():
    """Return grammar sites sorted by SITE_ID."""
    return discover_grammar_sites()


def _build_registry():
    sites = discover_grammar_sites()
    grammar_by_id = {g.SITE_ID: g for g in sites}
    registry = {}
    for g in sites:
        for domain in g.DOMAINS:
            registry[normalize_domain(domain)] = g
    return sites, registry, grammar_by_id


PARSERS, REGISTRY, GRAMMAR_BY_ID = _build_registry()


def refresh():
    """Re-scan grammar files (useful in tests / long-running processes)."""
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
    g = GRAMMAR_BY_ID.get(site_id)
    if g is not None:
        return g
    known = ", ".join(m.SITE_ID for m in PARSERS) or "(none)"
    raise UnsupportedSiteError(
        f"No parser with id '{site_id}'. Known: {known}"
    )


def get_parser(url: str):
    """
    Resolve a site parser for `url`.
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
    """
    from .engine import compare_candidates

    mode = (engine or PARSER_ENGINE or "grammar").strip().lower()
    site_id = getattr(mod, "SITE_ID", None)
    grammar = get_grammar_by_id(site_id) if site_id else None
    if grammar is None and isinstance(mod, GrammarSite):
        grammar = mod

    if mode == "grammar":
        if grammar is None:
            raise UnsupportedSiteError(
                f"No grammar for '{site_id}'. Add parsers/grammar/{site_id}.yaml"
            )
        return grammar.extract(soup, base_url), None

    if mode == "both":
        # Compare facade extract vs reloaded grammar (sanity / drift check).
        primary = mod.extract(soup, base_url)
        if grammar is None:
            return primary, {
                "ok": False,
                "only_py": [],
                "only_grammar": [],
                "score_mismatch": [],
                "missing_grammar": True,
            }
        secondary = grammar.extract(soup, base_url)
        return primary, compare_candidates(primary, secondary)

    # Legacy ``py`` alias: same as grammar now that site modules are gone.
    if grammar is None:
        return mod.extract(soup, base_url), None
    return grammar.extract(soup, base_url), None
