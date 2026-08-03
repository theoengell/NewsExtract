"""
Site parser registry with folder auto-discovery.

Drop a new ``parsers/<name>.py`` module that defines:

  NAME         - human-readable site name
  DEFAULT_URL  - front-page URL to fetch
  DOMAINS      - optional tuple of hostnames (without leading www.)
  extract(soup, base_url) -> list[(text, score, href, pos)]

Modules named ``base`` or starting with ``_`` are ignored.
"""

from __future__ import annotations

import importlib
import pkgutil
from urllib.parse import urlparse

from .base import normalize_domain


class UnsupportedSiteError(ValueError):
    """Raised when no parser is registered for a URL's domain."""


def _is_parser_module(mod) -> bool:
    return (
        hasattr(mod, "extract")
        and callable(mod.extract)
        and hasattr(mod, "NAME")
        and hasattr(mod, "DEFAULT_URL")
    )


def discover_parsers():
    """
    Import every parser module under this package.
    Returns a list of modules sorted by SITE_ID (module name).
    """
    package = importlib.import_module(__name__)
    found = []
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_") or info.name == "base":
            continue
        mod = importlib.import_module(f".{info.name}", __name__)
        if not _is_parser_module(mod):
            continue
        # Stable id = filename stem; used in sites.json / HTML data attributes.
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


def _build_registry():
    registry = {}
    modules = discover_parsers()
    for mod in modules:
        for domain in getattr(mod, "DOMAINS", ()):
            registry[normalize_domain(domain)] = mod
    return modules, registry


# Populated at import time; call refresh() after adding a parser in the same process.
PARSERS, REGISTRY = _build_registry()


def refresh():
    """Re-scan the parsers/ folder (useful in tests / long-running processes)."""
    global PARSERS, REGISTRY
    PARSERS, REGISTRY = _build_registry()
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
    raise UnsupportedSiteError(
        f"No parser with id '{site_id}'. Known: {', '.join(m.SITE_ID for m in PARSERS) or '(none)'}"
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
