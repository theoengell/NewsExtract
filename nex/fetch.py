"""HTTP fetch helpers and site logo download."""

from __future__ import annotations

import os
import re
import sys
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from parsers.base import normalize_domain

from .constants import DEFAULT_HEADERS, DEFAULT_LOGOS_DIR

def fetch_html(url: str, timeout: int = 15) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
    resp.raise_for_status()
    # When Content-Type has no charset, requests assumes ISO-8859-1. Sites like
    # TV 2 serve UTF-8 without declaring it, which mojibakes æ/ø/å.
    content_type = resp.headers.get("Content-Type", "")
    if "charset=" not in content_type.lower():
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text

_ICON_REL_SCORE = {
    "apple-touch-icon": 100,
    "apple-touch-icon-precomposed": 95,
    "icon": 50,
    "shortcut icon": 40,
    "mask-icon": 20,
}

def discover_logo_url(soup, base_url):
    """Pick the best favicon / touch-icon URL from page <link> tags."""
    candidates = []
    for link in soup.find_all("link", href=True):
        rels = [r.lower() for r in (link.get("rel") or [])]
        if not rels:
            continue
        rel_joined = " ".join(rels)
        score = 0
        for key, value in _ICON_REL_SCORE.items():
            if key in rel_joined or key in rels:
                score = max(score, value)
        if score <= 0:
            continue
        href = (link.get("href") or "").strip()
        if not href or href.startswith("data:"):
            continue
        sizes = (link.get("sizes") or "").lower()
        m = re.search(r"(\d+)", sizes)
        if m:
            score += min(int(m.group(1)), 192)
        elif "svg" in (link.get("type") or "").lower() or href.lower().endswith(".svg"):
            score += 64
        candidates.append((score, urljoin(base_url, href)))

    if candidates:
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return candidates[0][1]

    parsed = urlparse(base_url)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
    return None


def _logo_ext_from_response(url, content_type, content):
    ct = (content_type or "").split(";")[0].strip().lower()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    if ct in mapping:
        return mapping[ct]
    path = urlparse(url).path.lower()
    for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico"):
        if path.endswith(ext):
            return ".jpg" if ext == ".jpeg" else ext
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if content[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return ".webp"
    if content.lstrip().startswith((b"<svg", b"<?xml")):
        return ".svg"
    return ".ico"


def _existing_logo_path(logos_dir, site_id):
    if not os.path.isdir(logos_dir):
        return None
    prefix = site_id + "."
    for name in sorted(os.listdir(logos_dir)):
        if name.startswith(prefix) and not name.endswith(".tmp"):
            return os.path.join(logos_dir, name)
    return None


def download_logo_bytes(logo_url, timeout=12, max_bytes=512_000):
    resp = requests.get(logo_url, headers=DEFAULT_HEADERS, timeout=timeout, stream=True)
    resp.raise_for_status()
    chunks = []
    total = 0
    for chunk in resp.iter_content(8192):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f"logo too large (>{max_bytes} bytes)")
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise ValueError("empty logo response")
    return data, resp.headers.get("Content-Type", "")


def ensure_site_logo(site_id, site_url, logos_dir=DEFAULT_LOGOS_DIR, page_html=None, refresh=False):
    """
    Ensure logos/<site_id>.* exists. Reuses a local file when present unless
    refresh=True. Returns a path relative to the HTML file (e.g. 'logos/dr.png'),
    or None if unavailable.
    """
    os.makedirs(logos_dir, exist_ok=True)
    existing = _existing_logo_path(logos_dir, site_id)
    if existing and not refresh:
        return os.path.join(logos_dir, os.path.basename(existing)).replace("\\", "/")

    soup = None
    if page_html:
        soup = BeautifulSoup(page_html, "html.parser")
    else:
        try:
            page_html = fetch_html(site_url, timeout=12)
            soup = BeautifulSoup(page_html, "html.parser")
        except (requests.RequestException, OSError):
            soup = None

    candidates = []
    if soup is not None:
        found = discover_logo_url(soup, site_url)
        if found:
            candidates.append(found)
    parsed = urlparse(site_url)
    if parsed.scheme and parsed.netloc:
        favicon = f"{parsed.scheme}://{parsed.netloc}/favicon.ico"
        if favicon not in candidates:
            candidates.append(favicon)
        domain = normalize_domain(parsed.netloc)
        if domain:
            candidates.append(f"https://www.google.com/s2/favicons?domain={domain}&sz=64")

    last_error = None
    for logo_url in candidates:
        try:
            data, content_type = download_logo_bytes(logo_url)
            ext = _logo_ext_from_response(logo_url, content_type, data)
            # Drop prior files for this site (extension may have changed).
            for name in list(os.listdir(logos_dir)):
                if name.startswith(site_id + ".") and not name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(logos_dir, name))
                    except OSError:
                        pass
            dest = os.path.join(logos_dir, site_id + ext)
            tmp = dest + ".tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            rel = os.path.join(logos_dir, site_id + ext).replace("\\", "/")
            print(f"Downloaded logo for {site_id} → {rel}", file=sys.stderr)
            return rel
        except (requests.RequestException, OSError, ValueError) as e:
            last_error = e
            continue

    if existing:
        return os.path.join(logos_dir, os.path.basename(existing)).replace("\\", "/")
    if last_error:
            print(f"Warning: no logo for {site_id} ({last_error})", file=sys.stderr)
    return None


def _is_brand_or_keep_logo(name):
    """True for NewsExtract brand assets and placeholder keep files."""
    if name in (".gitkeep", ".gitignore"):
        return True
    return name.startswith("newsextract.") or name.startswith("newsextract_")


def clean_downloaded_logos(logos_dir=DEFAULT_LOGOS_DIR):
    """
    Remove publisher favicons from logos_dir. Keeps brand PNGs and .gitkeep.
    Returns list of removed paths.
    """
    removed = []
    if not os.path.isdir(logos_dir):
        return removed
    for name in list(os.listdir(logos_dir)):
        if _is_brand_or_keep_logo(name):
            continue
        path = os.path.join(logos_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed.append(path.replace("\\", "/"))
        except OSError as e:
            print(f"Warning: couldn't remove {path} ({e})", file=sys.stderr)
    return removed
