"""HTML dashboard builder and related helpers."""

from __future__ import annotations

import html
import json
import re
from difflib import SequenceMatcher
from urllib.parse import unquote, urlparse

from parsers.base import normalize_domain

from .config import (
    cluster_ignore_lookup,
    get_bg_color,
    language_label,
    load_cluster_ignore_words,
    match_category,
    normalize_languages,
    normalize_site_order,
    site_language,
)
from .constants import APP_VERSION, DEFAULT_CLUSTER_IGNORE_FILE, KNOWN_LANGUAGES

def is_external_href(href, base_url, allowed_domains=None):
    """
    True if href points at a host outside this news site's domains.
    Relative URLs (no host) count as internal.
    """
    host = normalize_domain(urlparse(href or "").netloc)
    if not host:
        return False

    allowed = {normalize_domain(d) for d in (allowed_domains or ()) if d}
    if base_url:
        base_host = normalize_domain(urlparse(base_url).netloc)
        if base_host:
            allowed.add(base_host)

    for allowed_host in allowed:
        if not allowed_host:
            continue
        if (
            host == allowed_host
            or host.endswith("." + allowed_host)
            or allowed_host.endswith("." + host)
        ):
            return False
    return True


# Path segments that are structural, not article-title language.
_URL_TECH_SEGMENTS = {
    "www", "index", "html", "htm", "php", "asp", "aspx", "ece",
    "article", "articles", "story", "stories", "content", "node",
    "amp", "print", "preview", "live", "video", "reels", "rss", "feed",
    "tag", "tags", "category", "categories", "author", "authors",
}

_URL_ID_SEG = re.compile(r"^(art|article)?\d+$", re.I)
_URL_HAS_WORDS = re.compile(r"[-_]")


def url_language_tooltip(href):
    """
    If the URL path contains a near-natural-language article slug, return a
    human-readable version (tech stripped, -/_ -> spaces) for use as a tooltip.
    Returns None when the URL is not descriptive enough.
    """
    if not href:
        return None

    path = unquote(urlparse(href).path or "")
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    # Drop trailing technical / id segments.
    while parts:
        last = parts[-1]
        low = last.lower()
        if (
            _URL_ID_SEG.match(low)
            or low in _URL_TECH_SEGMENTS
            or low.endswith((".html", ".htm", ".ece", ".php"))
            or (low.isdigit() and len(low) >= 4)
        ):
            parts.pop()
            continue
        # Strip extension from last segment if present.
        stem, _, ext = last.rpartition(".")
        if ext.lower() in ("html", "htm", "ece", "php", "aspx") and stem:
            parts[-1] = stem
        break

    if not parts:
        return None

    # Prefer the longest hyphenated/underscored segment (usually the title slug).
    candidates = []
    for seg in parts:
        low = seg.lower()
        if low in _URL_TECH_SEGMENTS or _URL_ID_SEG.match(low):
            continue
        if not _URL_HAS_WORDS.search(seg):
            continue
        words = [w for w in re.split(r"[-_]+", seg) if w]
        if len(words) < 2:
            continue
        # Need enough "language" signal: several words or a long phrase.
        if len(words) < 3 and len(seg) < 18:
            continue
        candidates.append(seg)

    if not candidates:
        return None

    slug = max(candidates, key=len)
    text = re.sub(r"[-_]+", " ", slug)
    text = re.sub(r"\s+", " ", text).strip()
    # Ignore if still looks technical / too short after cleanup.
    if len(text) < 12 or text.isdigit():
        return None
    return text


def _fold_for_tooltip_compare(text):
    """Normalize + fold so URL slugs compare fairly to headlines.

    Hyphens become spaces so '22-årig' and '22 årig' tokenize the same way
    before Danish letter folding.
    """
    t = (text or "").lower()
    t = t.replace("\u2019", "'").replace("\u2018", "'")
    t = t.replace("\u201c", '"').replace("\u201d", '"')
    t = t.replace("-", " ").replace("_", " ")
    t = re.sub(r"[^\w\s]", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    for src, dst in (
        ("æ", "ae"),
        ("ø", "oe"),
        ("å", "aa"),
        ("ä", "ae"),
        ("ö", "oe"),
        ("ü", "ue"),
    ):
        t = t.replace(src, dst)
    return t


def _tooltip_word_count(text):
    folded = _fold_for_tooltip_compare(text)
    return len([w for w in folded.split() if w])


def tooltip_matches_headline(tip, headline, threshold=0.72):
    """
    True when the URL-derived tooltip is close enough to the headline that
    showing it adds no useful information.

    If the URL wording has more words than the headline (after hyphen/space
    normalization), keep the tip — it may add detail the headline omitted.
    """
    if not tip or not headline:
        return False
    a = _fold_for_tooltip_compare(tip)
    b = _fold_for_tooltip_compare(headline)
    if not a or not b:
        return False
    tip_n = len([w for w in a.split() if w])
    head_n = len([w for w in b.split() if w])
    if tip_n > head_n:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 12 and shorter in longer:
        return True
    if SequenceMatcher(None, a, b).ratio() >= threshold:
        return True
    # Word overlap: most slug words already appear in the headline.
    tip_words = [w for w in a.split() if len(w) >= 3]
    if len(tip_words) >= 3:
        head_words = set(b.split())
        hits = sum(1 for w in tip_words if w in head_words)
        if hits / len(tip_words) >= 0.7:
            return True
    return False


def parse_highlight_words(raw):
    """Split a comma-separated highlight string into cleaned lowercase keywords."""
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        parts = raw
    else:
        parts = str(raw).split(",")
    words = []
    seen = set()
    for part in parts:
        w = re.sub(r"\s+", " ", str(part)).strip().casefold()
        if len(w) < 2 or w in seen:
            continue
        seen.add(w)
        words.append(w)
    return words


def headline_matches_keywords(text, keywords):
    if not keywords or not text:
        return False
    hay = text.casefold()
    return any(k in hay for k in keywords)


def _render_headline_li(
    text,
    href,
    categories,
    base_url,
    allowed_domains,
    show_external=True,
    is_new=True,
    highlight_words=None,
    exclude_words=None,
    seen_index=None,
    site_id=None,
    language=None,
    source_badge_html="",
):
    keywords = highlight_words if highlight_words is not None else []
    exclude = exclude_words if exclude_words is not None else []
    cat_id, meta = match_category(href, categories)
    color = meta.get("color")
    cat_enabled = bool(meta.get("enabled", True))
    external = is_external_href(href, base_url, allowed_domains)
    style = f' style="--hl-bg: {html.escape(color)};"' if color else ""
    classes = ["headline"]
    if not cat_enabled:
        classes.append("hidden-cat")
    if external and not show_external:
        classes.append("hidden-external")
    if headline_matches_keywords(text, exclude):
        classes.append("hidden-exclude")
    if headline_matches_keywords(text, keywords):
        classes.append("hl-keyword")
    ext_attr = "1" if external else "0"
    new_attr = "1" if is_new else "0"
    badge = ' <span class="ext-badge" title="Off-site link">↗</span>' if external else ""
    tip = url_language_tooltip(href)
    if tip and tooltip_matches_headline(tip, text):
        tip = None
    title_attr = f' title="{html.escape(tip, quote=True)}"' if tip else ""
    tip_html = (
        f'<span class="url-tip"{title_attr}>{html.escape(tip)}</span>'
        if tip
        else ""
    )
    index_attr = "" if seen_index is None else f' data-seen-index="{seen_index}"'
    site_attr = f' data-site="{html.escape(site_id)}"' if site_id else ""
    lang_attr = f' data-lang="{html.escape(language)}"' if language else ""
    href_attr = f' data-href="{html.escape(href, quote=True)}"'
    return (
        f'        <li class="{" ".join(classes)}" data-category="{html.escape(cat_id)}" '
        f'data-external="{ext_attr}" data-new="{new_attr}"{site_attr}{lang_attr}{href_attr}{index_attr}{style}>'
        f'{source_badge_html}'
        f'<span class="hl-main"><a href="{html.escape(href)}" target="_blank" '
        f'rel="noopener"{title_attr}>{html.escape(text)}</a>{badge}</span>'
        f'{tip_html}</li>'
    )


def _render_item_list(
    results,
    categories,
    base_url,
    allowed_domains,
    show_external=True,
    is_new=True,
    highlight_words=None,
    exclude_words=None,
    site_id=None,
    language=None,
):
    rows = []
    for idx, (text, href) in enumerate(results):
        rows.append(
            _render_headline_li(
                text,
                href,
                categories,
                base_url,
                allowed_domains,
                show_external=show_external,
                is_new=is_new,
                highlight_words=highlight_words,
                exclude_words=exclude_words,
                seen_index=None if is_new else idx,
                site_id=site_id,
                language=language,
            )
        )
    return "\n".join(rows) if rows else '        <li class="empty">No headlines</li>'


def _source_badge_html(block):
    name = html.escape(block["name"])
    logo_path = block.get("logo")
    if logo_path:
        return (
            f'<span class="src-badge" title="{name}">'
            f'<img src="{html.escape(logo_path, quote=True)}" alt="" width="14" height="14">'
            f'<span class="src-name">{name}</span></span>'
        )
    return f'<span class="src-badge" title="{name}"><span class="src-name">{name}</span></span>'


def _render_all_new_section(
    site_blocks,
    categories,
    site_domains,
    show_external,
    highlight_keywords,
    show_all_new,
    languages=None,
    exclude_keywords=None,
):
    langs = languages if isinstance(languages, dict) else {}
    exclude = exclude_keywords if exclude_keywords is not None else []
    rows = []
    for block in site_blocks:
        if block.get("error"):
            continue
        sid = block["site_id"]
        lang = block.get("language") or "da"
        if block.get("is_first_run"):
            items = block.get("seen_results") or []
        else:
            items = block.get("new_results") or []
        if not items:
            continue
        allowed = site_domains.get(sid) or ()
        badge = _source_badge_html(block)
        lang_hidden = langs.get(lang, True) is False
        for text, href in items:
            li = _render_headline_li(
                text,
                href,
                categories,
                block["url"],
                allowed,
                show_external=show_external,
                is_new=True,
                highlight_words=highlight_keywords,
                exclude_words=exclude,
                site_id=sid,
                language=lang,
                source_badge_html=badge,
            )
            if lang_hidden:
                li = li.replace('class="headline', 'class="headline hidden-lang', 1)
            rows.append(li)

    hidden_class = "" if show_all_new else " hidden"
    if not rows:
        body = '      <p class="empty">No new headlines across enabled sites.</p>'
        count = 0
    else:
        body = (
            f'      <ol class="all-new-list">\n'
            + "\n".join(rows)
            + "\n      </ol>"
        )
        count = len(rows)

    return f"""  <section class="all-new{hidden_class}" id="all-new" data-panel="all-new" data-collapse-id="all-new">
    <h2 class="site-title">
      <button type="button" class="site-collapse-toggle" aria-expanded="true" aria-controls="all-new-body" title="Collapse or expand">
        <span class="site-name">All new</span>
      </button>
      <span class="count-inline" id="all-new-count">({count})</span>
    </h2>
    <div class="site-body" id="all-new-body">
      <div class="site-body-inner">
{body}
      </div>
    </div>
  </section>"""


def build_combined_html(
    site_blocks,
    categories,
    sites_config,
    settings,
    site_domains,
    update_notice=None,
    cluster_ignore_path=None,
):
    """
    site_blocks: list of dicts with keys:
      site_id, name, url, new_results, seen_results, is_first_run, error (optional)
    site_domains: site_id -> iterable of allowed hostnames
    """
    if not isinstance(settings, dict):
        settings = {}
    show_external = bool(settings.get("show_external", True))
    only_new = bool(settings.get("only_new", False))
    show_all_new = bool(settings.get("show_all_new", True))
    dim_opened = bool(settings.get("dim_opened", True))
    show_opened_today = bool(settings.get("show_opened_today", True))
    dark_mode = bool(settings.get("dark_mode", False))
    show_clusters = bool(settings.get("show_clusters", False))
    cluster_view = str(settings.get("cluster_view") or "list").strip().lower()
    if cluster_view not in ("list", "graph"):
        cluster_view = "list"
    try:
        cluster_min_size = int(settings.get("cluster_min_size", 2))
    except (TypeError, ValueError):
        cluster_min_size = 2
    cluster_min_size = max(1, min(10, cluster_min_size))
    ignore_by_lang = load_cluster_ignore_words(
        cluster_ignore_path or DEFAULT_CLUSTER_IGNORE_FILE
    )
    ignore_lookup = cluster_ignore_lookup(ignore_by_lang)
    # Always drop news-section / local-boilerplate tokens that frequency lists miss.
    for extra in (
        "internationalt", "international", "national", "nationalt", "samfund",
        "content", "danmark", "denmark", "danish", "dansk", "danske", "danmarks",
        "kobenhavn", "koebenhavn", "københavn", "copenhagen", "aarhus", "århus",
        "aarig", "aarige", "arig", "arige", "amp", "amphtml", "virksomheder",
        "ece", "art", "cid", "politik", "udland", "indland", "nyheder", "nyhed",
    ):
        ignore_lookup[extra] = 1
    ignore_words_literal = json.dumps(ignore_lookup, ensure_ascii=False)
    available_langs = sorted({
        site_language(sites_config.get(b["site_id"]), b.get("language") or "da")
        for b in site_blocks
    } | set(KNOWN_LANGUAGES))
    languages = normalize_languages(settings.get("languages"), available_langs)
    try:
        bg_strength = int(settings.get("bg_strength", 35))
    except (TypeError, ValueError):
        bg_strength = 35
    bg_strength = max(0, min(100, bg_strength))
    try:
        seen_limit = int(settings.get("seen_limit", 15))
    except (TypeError, ValueError):
        seen_limit = 15
    seen_limit = max(0, min(500, seen_limit))
    highlight_words_raw = settings.get("highlight_words", "")
    if isinstance(highlight_words_raw, (list, tuple)):
        highlight_words_raw = ", ".join(str(w) for w in highlight_words_raw)
    else:
        highlight_words_raw = str(highlight_words_raw or "")
    highlight_keywords = parse_highlight_words(highlight_words_raw)
    exclude_words_raw = settings.get("exclude_words", "")
    if isinstance(exclude_words_raw, (list, tuple)):
        exclude_words_raw = ", ".join(str(w) for w in exclude_words_raw)
    else:
        exclude_words_raw = str(exclude_words_raw or "")
    exclude_keywords = parse_highlight_words(exclude_words_raw)
    site_order = normalize_site_order(
        settings.get("site_order"),
        [block["site_id"] for block in site_blocks],
    )
    order_index = {sid: i for i, sid in enumerate(site_order)}
    site_blocks = sorted(
        site_blocks,
        key=lambda b: (order_index.get(b["site_id"], 10_000), b["site_id"]),
    )

    site_toggle_rows = []
    sections = []
    initial_sites = {}
    site_lang_map = {}

    for block in site_blocks:
        sid = block["site_id"]
        entry = sites_config.get(sid, {})
        lang = site_language(entry, block.get("language") or "da")
        site_lang_map[sid] = lang
        lang_on = languages.get(lang, True)
        enabled = bool(entry.get("enabled", True))
        initial_sites[sid] = enabled
        checked = " checked" if enabled else ""
        hidden_class = "" if (enabled and lang_on) else " hidden"
        allowed = site_domains.get(sid) or ()
        logo_path = block.get("logo")
        if logo_path:
            toggle_logo = (
                f'<img class="site-logo-sm" src="{html.escape(logo_path, quote=True)}" '
                f'alt="" width="16" height="16">'
            )
        else:
            toggle_logo = ""
        site_toggle_rows.append(
            f'      <label class="toggle site-toggle" data-site="{html.escape(sid)}" '
            f'data-lang="{html.escape(lang)}" draggable="true">'
            f'<span class="drag-handle" title="Drag to reorder" aria-hidden="true">⋮⋮</span>'
            f'<input type="checkbox" data-site="{html.escape(sid)}"{checked}> '
            f'{toggle_logo}{html.escape(block["name"])}'
            f'<span class="lang-tag">{html.escape(language_label(lang))}</span></label>'
        )

        if block.get("error"):
            body = f'    <p class="error">{html.escape(block["error"])}</p>'
        elif block["is_first_run"]:
            # First run has no baseline; treat all as new so "only new" still shows them.
            body = f"""    <div class="freshness-block" data-freshness="new">
    <div class="count">{len(block["seen_results"])} headlines (first run)</div>
    <ol>
{_render_item_list(block["seen_results"], categories, block["url"], allowed, show_external, is_new=True, highlight_words=highlight_keywords, exclude_words=exclude_keywords, site_id=sid, language=lang)}
    </ol>
    </div>"""
        else:
            new_html = (
                f"""    <div class="freshness-block" data-freshness="new">
    <h3>New since last run ({len(block["new_results"])})</h3>
    <ol class="new">
{_render_item_list(block["new_results"], categories, block["url"], allowed, show_external, is_new=True, highlight_words=highlight_keywords, exclude_words=exclude_keywords, site_id=sid, language=lang)}
    </ol>
    </div>"""
                if block["new_results"]
                else """    <div class="freshness-block" data-freshness="new">
    <h3>New since last run (0)</h3>
    <p class="empty">No new headlines since last run.</p>
    </div>"""
            )
            seen_total = len(block["seen_results"])
            seen_html = (
                f"""    <div class="freshness-block" data-freshness="seen" data-seen-total="{seen_total}" data-seen-page="0">
    <h3 class="seen-heading">Previously seen (<span class="seen-shown">{min(seen_limit, seen_total)}</span> of <span class="seen-total">{seen_total}</span>)</h3>
    <ol>
{_render_item_list(block["seen_results"], categories, block["url"], allowed, show_external, is_new=False, highlight_words=highlight_keywords, exclude_words=exclude_keywords, site_id=sid, language=lang)}
    </ol>
    <nav class="seen-pager" aria-label="Previously seen pages">
      <button type="button" class="seen-prev">Previous {seen_limit}</button>
      <span class="seen-page-label">(1 of 1)</span>
      <button type="button" class="seen-next">Next {seen_limit}</button>
    </nav>
    </div>"""
                if block["seen_results"]
                else ""
            )
            body = new_html + "\n" + seen_html

        if logo_path:
            logo_html = (
                f'<img class="site-logo" src="{html.escape(logo_path, quote=True)}" '
                f'alt="" width="40" height="40" loading="lazy">'
            )
        else:
            logo_html = ""

        if block.get("error"):
            new_count = 0
        elif block["is_first_run"]:
            new_items = block["seen_results"]
            new_count = sum(
                1 for text, _href in new_items
                if not headline_matches_keywords(text, exclude_keywords)
            )
        else:
            new_items = block["new_results"]
            new_count = sum(
                1 for text, _href in new_items
                if not headline_matches_keywords(text, exclude_keywords)
            )
        if new_count == 1:
            new_count_html = '<span class="collapsed-new-count">1 new article</span>'
        elif new_count > 1:
            new_count_html = (
                f'<span class="collapsed-new-count">{new_count} new articles</span>'
            )
        else:
            new_count_html = ""

        body_id = f"site-body-{sid}"
        sections.append(
            f"""  <section class="site{hidden_class}" id="site-{html.escape(sid)}" """
            f"""data-site="{html.escape(sid)}" data-lang="{html.escape(lang)}">
    <h2 class="site-title">
      <button type="button" class="site-collapse-toggle" aria-expanded="true" """
            f"""aria-controls="{html.escape(body_id)}" title="Collapse or expand">
        {logo_html}<span class="site-name">{html.escape(block["name"])}</span>
      </button>
      {new_count_html}
    </h2>
    <div class="site-body" id="{html.escape(body_id)}">
      <div class="site-body-inner">
{body}
      </div>
    </div>
  </section>"""
        )

    all_new_section = _render_all_new_section(
        site_blocks,
        categories,
        site_domains,
        show_external,
        highlight_keywords,
        show_all_new,
        languages=languages,
        exclude_keywords=exclude_keywords,
    )
    clusters_hidden = "" if show_clusters else " hidden"
    list_pressed = "true" if cluster_view == "list" else "false"
    graph_pressed = "true" if cluster_view == "graph" else "false"
    list_view_class = "" if cluster_view == "list" else " hidden"
    graph_view_class = "" if cluster_view == "graph" else " hidden"
    clusters_section = f"""  <section class="clusters{clusters_hidden}" id="clusters" data-panel="clusters" data-collapse-id="clusters" data-cluster-view="{html.escape(cluster_view)}" data-cluster-min-size="{cluster_min_size}">
    <h2 class="site-title">
      <button type="button" class="site-collapse-toggle" aria-expanded="true" aria-controls="clusters-body" title="Collapse or expand">
        <span class="site-name">Clusters</span>
      </button>
      <span class="count-inline" id="clusters-count">(0)</span>
      <span class="cluster-min-size-control" title="Minimum articles in a cluster">
        <span class="cluster-min-size-label">Min</span>
        <button type="button" class="btn-cluster-step" id="btn-cluster-min-dec" aria-label="Decrease minimum cluster size">−</button>
        <span class="cluster-min-size-value" id="cluster-min-size-value">{cluster_min_size}</span>
        <button type="button" class="btn-cluster-step" id="btn-cluster-min-inc" aria-label="Increase minimum cluster size">+</button>
      </span>
      <span class="cluster-view-toggles" role="group" aria-label="Cluster presentation">
        <button type="button" class="btn-cluster-view" id="btn-cluster-list" aria-pressed="{list_pressed}">List</button>
        <button type="button" class="btn-cluster-view" id="btn-cluster-graph" aria-pressed="{graph_pressed}">Graph</button>
      </span>
    </h2>
    <div class="site-body" id="clusters-body">
      <div class="site-body-inner">
        <div id="clusters-list-view" class="clusters-list-view{list_view_class}">
          <div id="clusters-inner">
            <p class="empty">No shared topics among current headlines.</p>
          </div>
        </div>
        <div id="clusters-graph-view" class="clusters-graph-view{graph_view_class}">
          <canvas id="clusters-graph-canvas" width="900" height="480" aria-label="Cluster graph"></canvas>
          <p class="sites-hint" id="clusters-graph-hint">Drag nodes to rearrange. Click a node to list its articles below.</p>
          <div id="clusters-graph-detail" class="clusters-graph-detail">
            <h3 class="cluster-label" id="clusters-graph-detail-title">Select a cluster</h3>
            <ol id="clusters-graph-articles">
              <li class="empty">Click a cluster node to see articles.</li>
            </ol>
          </div>
        </div>
      </div>
    </div>
  </section>"""
    opened_hidden = "" if show_opened_today else " hidden"
    opened_today_section = f"""  <section class="opened-today{opened_hidden}" id="opened-today" data-panel="opened-today">
    <h2 class="site-title">Opened today <span class="count-inline" id="opened-today-count">(0)</span></h2>
    <ol id="opened-today-list">
      <li class="empty">No articles opened today.</li>
    </ol>
  </section>"""

    cat_toggle_rows = []
    initial_cats = {}
    sorted_cats = sorted(
        categories.items(),
        key=lambda item: (str(item[1].get("label") or item[0]).casefold(), item[0]),
    )
    for cat_id, meta in sorted_cats:
        initial_cats[cat_id] = bool(meta.get("enabled", True))
        checked = " checked" if initial_cats[cat_id] else ""
        color = html.escape(meta.get("color") or "#eee")
        label = html.escape(meta.get("label") or cat_id)
        cat_toggle_rows.append(
            f'      <label class="toggle cat-toggle" data-category="{html.escape(cat_id)}">'
            f'<input type="checkbox" data-category="{html.escape(cat_id)}"{checked}> '
            f'<span class="swatch" style="background:{color}"></span>{label}</label>'
        )

    ext_checked = " checked" if show_external else ""
    only_new_checked = " checked" if only_new else ""
    all_new_checked = " checked" if show_all_new else ""
    dim_opened_checked = " checked" if dim_opened else ""
    opened_today_checked = " checked" if show_opened_today else ""
    dark_mode_checked = " checked" if dark_mode else ""
    clusters_checked = " checked" if show_clusters else ""
    body_classes = []
    if dim_opened:
        body_classes.append("dim-opened")
    if dark_mode:
        body_classes.append("dark")
    body_class_attr = f' class="{" ".join(body_classes)}"' if body_classes else ""
    sites_json_literal = json.dumps(sites_config, ensure_ascii=False)
    settings_json_literal = json.dumps(settings, ensure_ascii=False)
    categories_json_literal = json.dumps(categories, ensure_ascii=False)
    initial_sites_literal = json.dumps(initial_sites)
    initial_cats_literal = json.dumps(initial_cats)
    initial_external_literal = json.dumps(show_external)
    initial_only_new_literal = json.dumps(only_new)
    initial_all_new_literal = json.dumps(show_all_new)
    initial_dim_opened_literal = json.dumps(dim_opened)
    initial_opened_today_literal = json.dumps(show_opened_today)
    initial_dark_mode_literal = json.dumps(dark_mode)
    initial_clusters_literal = json.dumps(show_clusters)
    initial_cluster_view_literal = json.dumps(cluster_view)
    initial_cluster_min_size_literal = json.dumps(cluster_min_size)
    initial_bg_strength_literal = json.dumps(bg_strength)
    initial_seen_limit_literal = json.dumps(seen_limit)
    initial_highlight_words_literal = json.dumps(highlight_words_raw, ensure_ascii=False)
    initial_exclude_words_literal = json.dumps(exclude_words_raw, ensure_ascii=False)
    initial_site_order_literal = json.dumps(site_order)
    initial_languages_literal = json.dumps(languages)
    site_lang_map_literal = json.dumps(site_lang_map)
    highlight_words_attr = html.escape(highlight_words_raw, quote=True)
    exclude_words_attr = html.escape(exclude_words_raw, quote=True)

    lang_toggle_rows = []
    for code, enabled in languages.items():
        checked = " checked" if enabled else ""
        lang_toggle_rows.append(
            f'      <label class="toggle">'
            f'<input type="checkbox" data-lang="{html.escape(code)}"{checked}> '
            f'{html.escape(language_label(code))}</label>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>NewsExtract</title>
<style>
  :root {{
    --bg: #f4f5f7;
    --panel: #ffffff;
    --text: #1a1a1a;
    --muted: #666;
    --border: #ddd;
    --accent: #1565c0;
    --link: #2c2c2c;
    --link-opened: #6e6e6e;
    --hover: #f0f0f0;
    --hover-soft: #f3f5f7;
    --empty: #999;
    --error: #b71c1c;
    --tip: #8a8f98;
    --subhead: #333;
    --btn-face: #fafafa;
    --hl-bg-strength: {bg_strength}%;
    --font-body: "Lato", Helvetica, Arial, sans-serif;
  }}
  body.dark {{
    --bg: #12151a;
    --panel: #1c2128;
    --text: #e8eaed;
    --muted: #9aa0a8;
    --border: #2f3640;
    --accent: #5b9fd4;
    --link: #d0d0d0;
    --link-opened: #8a8a8a;
    --hover: #2a313a;
    --hover-soft: #252b34;
    --empty: #7a828c;
    --error: #f28b82;
    --tip: #8b939e;
    --subhead: #c5cad1;
    --btn-face: #252b34;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-Regular.ttf") format("truetype");
    font-weight: 400;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-Italic.ttf") format("truetype");
    font-weight: 400;
    font-style: italic;
    font-display: swap;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-Bold.ttf") format("truetype");
    font-weight: 700;
    font-style: normal;
    font-display: swap;
  }}
  @font-face {{
    font-family: "Lato";
    src: url("fonts/Lato-BoldItalic.ttf") format("truetype");
    font-weight: 700;
    font-style: italic;
    font-display: swap;
  }}
  body {{
    font-family: var(--font-body);
    max-width: 840px;
    margin: 0 auto;
    padding: 24px 20px 60px;
    color: var(--text);
    background: var(--bg);
  }}
  .page-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 1.2em;
  }}
  .page-header .brand {{
    display: flex;
    align-items: center;
    gap: 0.65em;
    min-width: 0;
  }}
  .page-header .brand-logo {{
    width: 48px;
    height: 48px;
    object-fit: contain;
    flex: 0 0 auto;
    border-radius: 8px;
  }}
  .page-header h1 {{
    font-family: var(--font-body);
    font-size: 1.75rem;
    font-weight: 700;
    margin: 0;
    letter-spacing: 0.02em;
  }}
  .page-header h1 .brand-version {{
    font-size: 0.62rem;
    font-weight: 400;
    color: #808080;
    opacity: 0.5;
    margin-left: 0.45em;
    vertical-align: baseline;
    display: inline-block;
    line-height: 1;
    white-space: nowrap;
  }}
  .page-header h1 .version-update-link {{
    margin-left: 0.5em;
    font-size: 0.6rem;
    font-weight: 400;
    color: #808080;
    opacity: 0.5;
    text-decoration: underline;
    white-space: nowrap;
  }}
  .header-actions {{
    display: flex;
    align-items: center;
    gap: 0.5em;
    flex: 0 0 auto;
  }}
  .btn-settings,
  .btn-theme,
  .btn-cluster {{
    font: inherit;
    font-size: 0.9rem;
    padding: 0.4em 0.85em;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--panel);
    color: var(--text);
    cursor: pointer;
    box-shadow: 0 1px 2px rgba(0,0,0,0.04);
  }}
  .btn-settings:hover,
  .btn-theme:hover,
  .btn-cluster:hover {{ background: var(--hover); }}
  .btn-cluster[aria-pressed="true"] {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }}
  .btn-cluster[aria-pressed="true"]:hover {{ filter: brightness(1.05); background: var(--accent); }}
  .settings-backdrop {{
    display: none;
    position: fixed;
    inset: 0;
    z-index: 100;
    background: rgba(20, 24, 28, 0.45);
    align-items: flex-start;
    justify-content: center;
    padding: 8vh 16px 24px;
    overflow: auto;
  }}
  .settings-backdrop.open {{ display: flex; }}
  .settings-panel {{
    width: min(560px, 100%);
    max-height: min(80vh, 720px);
    overflow: auto;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 16px 18px 18px;
    box-shadow: 0 12px 40px rgba(0,0,0,0.18);
  }}
  .settings-panel-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.6em;
  }}
  .settings-panel-header h2 {{
    font-family: var(--font-body);
    font-size: 1.25rem;
    font-weight: 700;
    margin: 0;
    color: var(--text);
    text-transform: none;
    letter-spacing: 0.02em;
  }}
  .btn-close {{
    font: inherit;
    font-size: 1.2rem;
    line-height: 1;
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--btn-face);
    cursor: pointer;
    color: var(--muted);
  }}
  .btn-close:hover {{ background: var(--hover); color: var(--text); }}
  .settings-section-title {{
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--muted);
    margin: 1em 0 0.5em;
  }}
  .settings-section-title:first-of-type {{ margin-top: 0.2em; }}
  .toggles {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.45em 0.9em;
    margin-bottom: 0.5em;
  }}
  #site-toggles {{
    flex-direction: column;
    flex-wrap: nowrap;
    align-items: stretch;
    gap: 0.25em;
  }}
  #site-toggles .site-toggle {{
    display: flex;
    align-items: center;
    gap: 0.4em;
    padding: 0.3em 0.4em;
    border: 1px solid transparent;
    border-radius: 6px;
    cursor: grab;
  }}
  #site-toggles .site-toggle:hover {{
    background: var(--hover-soft);
  }}
  #site-toggles .site-toggle.dragging {{
    opacity: 0.45;
  }}
  #site-toggles .site-toggle.drag-over {{
    border-top-color: var(--accent);
  }}
  #site-toggles .drag-handle {{
    color: var(--muted);
    font-size: 0.85rem;
    letter-spacing: -0.12em;
    line-height: 1;
    padding: 0 0.15em;
    cursor: grab;
    flex: 0 0 auto;
  }}
  #site-toggles .site-logo-sm {{
    width: 16px;
    height: 16px;
    object-fit: contain;
    flex: 0 0 auto;
    border-radius: 2px;
  }}
  #site-toggles .lang-tag {{
    margin-left: auto;
    font-size: 0.72rem;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }}
  #site-toggles .site-toggle.hidden-lang {{
    display: none;
  }}
  .sites-hint {{
    font-size: 0.8rem;
    color: var(--muted);
    margin: 0 0 0.55em;
  }}
  .toggle {{
    display: inline-flex;
    align-items: center;
    gap: 0.35em;
    font-size: 0.92rem;
    cursor: pointer;
    user-select: none;
  }}
  .swatch {{
    width: 0.85em;
    height: 0.85em;
    border-radius: 3px;
    border: 1px solid rgba(0,0,0,0.15);
    flex-shrink: 0;
  }}
  .toolbar-actions {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.5em;
    align-items: center;
    margin-top: 0.35em;
  }}
  button {{
    font: inherit;
    font-size: 0.9rem;
    padding: 0.35em 0.75em;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--btn-face);
    color: var(--text);
    cursor: pointer;
  }}
  button:hover {{ background: var(--hover); }}
  button.primary {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }}
  button.primary:hover {{ filter: brightness(1.05); }}
  .hint {{ font-size: 0.8rem; color: var(--muted); margin-top: 0.7em; }}
  .status {{ font-size: 0.85rem; color: var(--accent); min-height: 1.2em; }}
  .slider-row {{
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 0.35em 0.75em;
    align-items: center;
    margin: 0.35em 0 0.6em;
  }}
  .slider-row label {{
    grid-column: 1 / -1;
    font-size: 0.92rem;
  }}
  .slider-row input[type="range"],
  .slider-row input[type="number"] {{
    width: 100%;
    accent-color: var(--accent);
  }}
  .slider-row input[type="number"] {{
    font: inherit;
    padding: 0.25em 0.4em;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--panel);
    color: var(--text);
  }}
  .slider-row .slider-value {{
    font-size: 0.85rem;
    color: var(--muted);
    min-width: 2.5em;
    text-align: right;
  }}
  .slider-row input[type="text"] {{
    grid-column: 1 / -1;
    font: inherit;
    padding: 0.35em 0.5em;
    border: 1px solid var(--border);
    border-radius: 4px;
    width: 100%;
    box-sizing: border-box;
    background: var(--panel);
    color: var(--text);
  }}
  body.settings-open {{ overflow: hidden; }}
  section.site {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1em 1.2em 1.2em;
    margin-bottom: 1.2em;
  }}
  section.site.hidden {{ display: none; }}
  section.site.collapsed {{
    padding-bottom: 1em;
  }}
  section.site.collapsed > .site-title {{ margin-bottom: 0; }}
  .site-body {{
    display: grid;
    grid-template-rows: 1fr;
    transition: grid-template-rows 0.28s ease;
  }}
  html:not(.collapse-ready) .site-body {{
    transition: none;
  }}
  @media (prefers-reduced-motion: reduce) {{
    .site-body {{ transition: none; }}
  }}
  .site-body > .site-body-inner {{
    overflow: hidden;
    min-height: 0;
  }}
  section.site.collapsed > .site-body,
  section.all-new.collapsed > .site-body,
  section.clusters.collapsed > .site-body {{
    grid-template-rows: 0fr;
  }}
  section.all-new,
  section.clusters,
  section.opened-today {{
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1em 1.2em 1.2em;
    margin-bottom: 1.2em;
  }}
  section.all-new.hidden,
  section.clusters.hidden,
  section.opened-today.hidden {{ display: none; }}
  section.all-new.collapsed,
  section.clusters.collapsed {{
    padding-bottom: 1em;
  }}
  section.all-new.collapsed > .site-title,
  section.clusters.collapsed > .site-title {{ margin-bottom: 0; }}
  .cluster-group {{
    margin: 0 0 1em;
    padding: 0 0 0.75em;
    border-bottom: 1px solid var(--border);
  }}
  .cluster-group:last-child {{
    margin-bottom: 0;
    padding-bottom: 0;
    border-bottom: none;
  }}
  .cluster-label {{
    font-family: var(--font-body);
    font-size: 1rem;
    font-weight: 700;
    margin: 0 0 0.45em;
    color: var(--accent);
    letter-spacing: 0.01em;
  }}
  .cluster-label .cluster-size {{
    font-weight: 500;
    font-size: 0.85em;
    color: var(--muted);
  }}
  .cluster-group ol {{
    list-style: none;
    margin: 0;
    padding: 0;
  }}
  .cluster-view-toggles {{
    display: inline-flex;
    gap: 0.3em;
    margin-left: auto;
    flex: 0 0 auto;
  }}
  section.clusters > .site-title {{
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 0.45em 0.75em;
  }}
  .btn-cluster-view {{
    font: inherit;
    font-size: 0.78rem;
    padding: 0.2em 0.65em;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--btn-face);
    color: var(--text);
    cursor: pointer;
  }}
  .btn-cluster-view:hover {{ background: var(--hover); }}
  .btn-cluster-view[aria-pressed="true"] {{
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }}
  .cluster-min-size-control {{
    display: inline-flex;
    align-items: center;
    gap: 0.25em;
    margin-left: 0.15em;
    font-size: 0.78rem;
    color: var(--muted);
  }}
  .cluster-min-size-label {{
    margin-right: 0.15em;
  }}
  .cluster-min-size-value {{
    min-width: 1.25em;
    text-align: center;
    font-weight: 700;
    color: var(--text);
  }}
  .btn-cluster-step {{
    font: inherit;
    font-size: 0.9rem;
    line-height: 1;
    width: 1.55em;
    height: 1.55em;
    padding: 0;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--btn-face);
    color: var(--text);
    cursor: pointer;
  }}
  .btn-cluster-step:hover {{ background: var(--hover); }}
  .btn-cluster-step:disabled {{
    opacity: 0.4;
    cursor: default;
  }}
  .clusters-graph-view {{
    margin-top: 0.25em;
  }}
  .clusters-graph-view.hidden,
  .clusters-list-view.hidden {{
    display: none;
  }}
  #clusters-graph-canvas {{
    display: block;
    width: 100%;
    height: min(56vh, 520px);
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--bg);
    cursor: grab;
    touch-action: none;
  }}
  #clusters-graph-canvas.is-dragging {{ cursor: grabbing; }}
  .clusters-graph-detail {{
    margin-top: 0.85em;
  }}
  .clusters-graph-detail .cluster-label {{
    margin-bottom: 0.5em;
  }}
  #clusters-graph-articles {{
    list-style: none;
    margin: 0;
    padding: 0;
  }}
  .count-inline {{
    color: var(--muted);
    font-weight: 500;
    font-size: 0.9em;
  }}
  .src-badge {{
    display: inline-flex;
    align-items: center;
    gap: 0.3em;
    flex: 0 0 auto;
    max-width: 7.5em;
    margin-right: 0.15em;
    color: var(--muted);
    font-size: 0.75em;
  }}
  .src-badge img {{
    width: 14px;
    height: 14px;
    object-fit: contain;
    border-radius: 2px;
  }}
  .src-badge .src-name {{
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  body.dim-opened li.headline.opened {{
    opacity: 0.42;
  }}
  body.dim-opened li.headline.opened .hl-main a {{
    color: var(--link-opened);
  }}
  li.headline.hidden-cat,
  li.headline.hidden-external,
  li.headline.hidden-site,
  li.headline.hidden-lang,
  li.headline.hidden-exclude {{ display: none; }}
  body.only-new .freshness-block[data-freshness="seen"] {{ display: none; }}
  body.only-new li.headline[data-new="0"] {{ display: none; }}
  li.headline.hidden-seen-limit {{ display: none; }}
  .freshness-block.hidden-seen-limit-block {{ display: none; }}
  .seen-pager {{
    display: none;
    align-items: center;
    justify-content: flex-end;
    gap: 0.6em;
    margin-top: 0.75em;
    flex-wrap: wrap;
    font-size: 0.72rem;
  }}
  .freshness-block[data-freshness="seen"].has-seen-pages > .seen-pager {{
    display: flex;
  }}
  .seen-pager .seen-page-label {{
    font-size: inherit;
    font-weight: 500;
    color: var(--muted);
    min-width: 4em;
    text-align: center;
  }}
  .seen-pager button {{
    font-size: inherit;
    padding: 0.2em 0.5em;
  }}
  .seen-pager button:disabled {{
    opacity: 0.45;
    cursor: default;
  }}
  .seen-pager button:disabled:hover {{
    background: var(--btn-face);
  }}
  li.seen-pad {{
    visibility: hidden;
    pointer-events: none;
    user-select: none;
    display: flex;
    align-items: baseline;
    gap: 0.55em;
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    margin: 0.35em 0;
    padding: 0.15em 0.4em;
    line-height: 1.4;
    border-radius: 4px;
  }}
  .ext-badge {{
    color: var(--muted);
    font-size: 0.75em;
    margin-left: 0.25em;
  }}
  h2.site-title {{
    display: flex;
    align-items: center;
    gap: 0.45em;
    font-family: var(--font-body);
    font-size: 1.35rem;
    font-weight: 700;
    margin: 0 0 0.6em;
  }}
  h2.site-title .site-collapse-toggle {{
    display: inline-flex;
    align-items: center;
    gap: 0.45em;
    margin: 0;
    padding: 0;
    border: none;
    background: transparent;
    color: inherit;
    font: inherit;
    font-weight: inherit;
    cursor: pointer;
    text-align: left;
  }}
  h2.site-title .site-collapse-toggle:focus-visible {{
    outline: 2px solid var(--link);
    outline-offset: 3px;
    border-radius: 4px;
  }}
  h2.site-title .collapsed-new-count {{
    display: none;
    margin-left: auto;
    font-size: 0.8rem;
    font-weight: 500;
    color: var(--muted);
    white-space: nowrap;
  }}
  section.site.collapsed > .site-title .collapsed-new-count {{
    display: inline;
  }}
  h2.site-title .site-logo {{
    width: 2.5rem;
    height: 2.5rem;
    max-width: 48px;
    max-height: 48px;
    object-fit: contain;
    flex: 0 0 auto;
    border-radius: 3px;
  }}
  h2 {{
    font-family: var(--font-body);
    font-size: 1.25rem;
    font-weight: 700;
    margin: 0 0 0.6em;
  }}
  h2 a {{ color: inherit; text-decoration: none; }}
  h2 a:hover {{ text-decoration: underline; }}
  h3 {{
    font-family: var(--font-body);
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--subhead);
    margin: 1em 0 0.4em;
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.25em;
  }}
  ol {{ padding-left: 1.4em; margin: 0.3em 0; }}
  li {{ margin: 0.35em 0; line-height: 1.4; padding: 0.15em 0.4em; border-radius: 4px; }}
  li.headline {{
    display: flex;
    align-items: baseline;
    gap: 0.55em;
    max-width: 100%;
    overflow: hidden;
    white-space: nowrap;
    background-color: color-mix(in srgb, var(--hl-bg, transparent) var(--hl-bg-strength), transparent);
  }}
  li.headline > .hl-main {{
    display: flex;
    align-items: baseline;
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
  }}
  li.headline:has(> .url-tip) > .hl-main {{
    flex: 0 1 auto;
    max-width: 70%;
  }}
  li.headline > .hl-main > a {{
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  li.headline.hl-keyword > .hl-main > a {{
    font-weight: 700;
  }}
  li.headline > .hl-main > .ext-badge {{
    flex: 0 0 auto;
  }}
  li.headline > .url-tip {{
    flex: 1 1 0;
    min-width: 3em;
    color: var(--tip);
    font-size: 0.82em;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .count {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 0.6em; }}
  .empty {{ color: var(--empty); font-style: italic; }}
  .error {{ color: var(--error); }}
  footer.page-footer {{
    margin: 2.5em 0 1.5em;
    text-align: center;
    font-size: 0.7rem;
    line-height: 1.4;
    color: var(--text);
    opacity: 0.3;
  }}
</style>
</head>
<body{body_class_attr}>
  <div class="page-header">
    <div class="brand">
      <img class="brand-logo" src="logos/newsextract_256.png" alt="" width="48" height="48">
      <h1>NewsExtract <span class="brand-version">v{html.escape(APP_VERSION)}</span>{(f' <a class="version-update-link" href="{html.escape(update_notice.get("url", ""), quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(update_notice.get("message", "New version available"))}</a>' if isinstance(update_notice, dict) and update_notice.get("url") else "")}</h1>
    </div>
    <div class="header-actions">
      <button type="button" class="btn-cluster" id="btn-cluster" aria-pressed="{str(show_clusters).lower()}" title="Group related headlines by shared keywords">Cluster</button>
      <button type="button" class="btn-theme" id="btn-theme" aria-pressed="{str(dark_mode).lower()}" title="Toggle dark / light mode">{("Light" if dark_mode else "Dark")}</button>
      <button type="button" class="btn-settings" id="btn-open-settings" aria-haspopup="dialog">Settings</button>
    </div>
  </div>

  <div class="settings-backdrop" id="settings-backdrop" role="presentation">
    <div class="settings-panel" role="dialog" aria-modal="true" aria-labelledby="settings-title" tabindex="-1">
      <div class="settings-panel-header">
        <h2 id="settings-title">Settings</h2>
        <button type="button" class="btn-close" id="btn-close-settings" aria-label="Close settings">&times;</button>
      </div>

      <div class="settings-section-title">Languages</div>
      <p class="sites-hint">Turn languages off to hide those news sources.</p>
      <div class="toggles" id="lang-toggles">
{chr(10).join(lang_toggle_rows)}
      </div>

      <div class="settings-section-title">Sites</div>
      <p class="sites-hint">Drag to reorder how sites appear on the page.</p>
      <div class="toggles" id="site-toggles">
{chr(10).join(site_toggle_rows)}
      </div>
      <div class="toolbar-actions">
        <button type="button" id="btn-sites-all">All on</button>
        <button type="button" id="btn-sites-none">All off</button>
      </div>

      <div class="settings-section-title">Categories</div>
      <div class="toggles" id="cat-toggles">
{chr(10).join(cat_toggle_rows)}
      </div>
      <div class="toolbar-actions">
        <button type="button" id="btn-cats-all">All on</button>
        <button type="button" id="btn-cats-none">All off</button>
      </div>

      <div class="settings-section-title">Filters</div>
      <div class="toggles" id="link-toggles">
        <label class="toggle">
          <input type="checkbox" id="toggle-external"{ext_checked}>
          Off-site links <span class="ext-badge">↗</span>
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-only-new"{only_new_checked}>
          Only new articles
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-all-new"{all_new_checked}>
          Show “All new” feed
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-clusters"{clusters_checked}>
          Show cluster view
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-dim-opened"{dim_opened_checked}>
          Dim opened links
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-opened-today"{opened_today_checked}>
          Show “Opened today”
        </label>
        <label class="toggle">
          <input type="checkbox" id="toggle-dark"{dark_mode_checked}>
          Dark mode
        </label>
      </div>
      <div class="slider-row">
        <label for="slider-bg-strength">Category color strength</label>
        <input type="range" id="slider-bg-strength" min="0" max="100" step="1" value="{bg_strength}">
        <span class="slider-value" id="slider-bg-strength-value">{bg_strength}%</span>
      </div>
      <div class="slider-row">
        <label for="input-seen-limit">Previously seen headlines shown (per site)</label>
        <input type="number" id="input-seen-limit" min="0" max="500" step="1" value="{seen_limit}">
        <span class="slider-value" id="input-seen-limit-value">{seen_limit}</span>
      </div>
      <div class="slider-row">
        <label for="input-highlight-words">Bold headlines containing (comma-separated)</label>
        <input type="text" id="input-highlight-words" value="{highlight_words_attr}"
          placeholder="e.g. trump, grønland, ukraine" autocomplete="off" spellcheck="false">
      </div>
      <div class="slider-row">
        <label for="input-exclude-words">Exclude headlines containing (comma-separated)</label>
        <input type="text" id="input-exclude-words" value="{exclude_words_attr}"
          placeholder="e.g. reality, influencers, royal" autocomplete="off" spellcheck="false">
      </div>

      <div class="toolbar-actions" style="margin-top:1em">
        <button type="button" class="primary" id="btn-export">Save for next run</button>
        <button type="button" id="btn-clear-storage">Clear all cookies &amp; saved settings</button>
        <span class="status" id="status"></span>
      </div>
      <p class="hint">Downloads updated <code>sites.json</code>, <code>settings.json</code>, and <code>categories.json</code>.
        Replace those files in the NewsExtract folder to apply on the next script run.
        “Clear all…” removes NewsExtract data stored in this browser (localStorage / cookies).</p>
    </div>
  </div>

{clusters_section}
{all_new_section}
{opened_today_section}
{chr(10).join(sections)}

<script>
(function () {{
  const SITE_KEY = "newsextract.siteVisibility";
  const CAT_KEY = "newsextract.categoryVisibility";
  const EXT_KEY = "newsextract.showExternal";
  const ONLY_NEW_KEY = "newsextract.onlyNew";
  const ALL_NEW_KEY = "newsextract.showAllNew";
  const CLUSTERS_KEY = "newsextract.showClusters";
  const DIM_OPENED_KEY = "newsextract.dimOpened";
  const OPENED_TODAY_KEY = "newsextract.showOpenedToday";
  const DARK_MODE_KEY = "newsextract.darkMode";
  const LANG_KEY = "newsextract.languages";
  const OPENED_LINKS_KEY = "newsextract.openedLinks";
  const COLLAPSED_KEY = "newsextract.collapsedSites";
  const sitesConfig = {sites_json_literal};
  const settingsConfig = {settings_json_literal};
  const categoriesConfig = {categories_json_literal};
  const initialSites = {initial_sites_literal};
  const initialCats = {initial_cats_literal};
  const initialExternal = {initial_external_literal};
  const initialOnlyNew = {initial_only_new_literal};
  const initialAllNew = {initial_all_new_literal};
  const initialClusters = {initial_clusters_literal};
  const initialClusterView = {initial_cluster_view_literal};
  const initialClusterMinSize = {initial_cluster_min_size_literal};
  const initialDimOpened = {initial_dim_opened_literal};
  const initialOpenedToday = {initial_opened_today_literal};
  const initialDarkMode = {initial_dark_mode_literal};
  const initialBgStrength = {initial_bg_strength_literal};
  const initialSeenLimit = {initial_seen_limit_literal};
  const initialHighlightWords = {initial_highlight_words_literal};
  const initialExcludeWords = {initial_exclude_words_literal};
  const initialSiteOrder = {initial_site_order_literal};
  const initialLanguages = {initial_languages_literal};
  const siteLangMap = {site_lang_map_literal};

  const backdrop = document.getElementById("settings-backdrop");
  const openBtn = document.getElementById("btn-open-settings");
  const closeBtn = document.getElementById("btn-close-settings");

  function openSettings() {{
    backdrop.classList.add("open");
    document.body.classList.add("settings-open");
    closeBtn.focus();
  }}

  function closeSettings() {{
    backdrop.classList.remove("open");
    document.body.classList.remove("settings-open");
    openBtn.focus();
  }}

  openBtn.addEventListener("click", openSettings);
  closeBtn.addEventListener("click", closeSettings);
  backdrop.addEventListener("click", function (e) {{
    if (e.target === backdrop) closeSettings();
  }});
  document.addEventListener("keydown", function (e) {{
    if (e.key === "Escape" && backdrop.classList.contains("open")) closeSettings();
  }});

    function loadState(key, fallback) {{
    try {{
      const raw = localStorage.getItem(key);
      if (!raw) return Object.assign({{}}, fallback);
      return Object.assign({{}}, fallback, JSON.parse(raw));
    }} catch (e) {{
      return Object.assign({{}}, fallback);
    }}
  }}

  function loadBool(key, fallback) {{
    try {{
      const raw = localStorage.getItem(key);
      if (raw === null || raw === "") return fallback;
      return JSON.parse(raw) !== false;
    }} catch (e) {{
      return fallback;
    }}
  }}

  function saveState(key, state) {{
    localStorage.setItem(key, JSON.stringify(state));
  }}

  function currentSiteState() {{
    const state = {{}};
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{
      state[cb.getAttribute("data-site")] = cb.checked;
    }});
    return state;
  }}

  function currentSiteOrder() {{
    return Array.prototype.map.call(
      document.querySelectorAll("#site-toggles .site-toggle[data-site]"),
      function (el) {{ return el.getAttribute("data-site"); }}
    );
  }}

  function applySiteOrder(order) {{
    const list = Array.isArray(order) ? order.slice() : [];
    const togglesRoot = document.getElementById("site-toggles");
    const toggleById = {{}};
    togglesRoot.querySelectorAll(".site-toggle[data-site]").forEach(function (el) {{
      toggleById[el.getAttribute("data-site")] = el;
    }});
    const seen = {{}};
    list.forEach(function (id) {{
      if (toggleById[id] && !seen[id]) {{
        togglesRoot.appendChild(toggleById[id]);
        seen[id] = true;
      }}
    }});
    Object.keys(toggleById).forEach(function (id) {{
      if (!seen[id]) togglesRoot.appendChild(toggleById[id]);
    }});

    const sectionById = {{}};
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      sectionById[sec.getAttribute("data-site")] = sec;
    }});
    const orderedSections = [];
    const sectionSeen = {{}};
    currentSiteOrder().forEach(function (id) {{
      if (sectionById[id] && !sectionSeen[id]) {{
        orderedSections.push(sectionById[id]);
        sectionSeen[id] = true;
      }}
    }});
    Object.keys(sectionById).forEach(function (id) {{
      if (!sectionSeen[id]) orderedSections.push(sectionById[id]);
    }});
    orderedSections.forEach(function (sec) {{
      const anchor = document.querySelector("footer.page-footer");
      if (anchor) document.body.insertBefore(sec, anchor);
      else document.body.appendChild(sec);
    }});

    const finalOrder = currentSiteOrder();
    const allNewList = document.querySelector("#all-new ol.all-new-list");
    if (allNewList) {{
      const items = Array.prototype.slice.call(
        allNewList.querySelectorAll("li.headline[data-site]")
      );
      const bySite = {{}};
      items.forEach(function (li) {{
        const id = li.getAttribute("data-site");
        if (!bySite[id]) bySite[id] = [];
        bySite[id].push(li);
      }});
      const used = {{}};
      finalOrder.forEach(function (id) {{
        if (!bySite[id] || used[id]) return;
        used[id] = true;
        bySite[id].forEach(function (li) {{ allNewList.appendChild(li); }});
      }});
      Object.keys(bySite).forEach(function (id) {{
        if (used[id]) return;
        bySite[id].forEach(function (li) {{ allNewList.appendChild(li); }});
      }});
    }}
    return finalOrder;
  }}

  function currentCatState() {{
    const state = {{}};
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{
      state[cb.getAttribute("data-category")] = cb.checked;
    }});
    return state;
  }}

  function currentExternalState() {{
    return document.getElementById("toggle-external").checked;
  }}

  function currentOnlyNewState() {{
    return document.getElementById("toggle-only-new").checked;
  }}

  function currentLangState() {{
    const state = {{}};
    document.querySelectorAll("#lang-toggles input[data-lang]").forEach(function (cb) {{
      state[cb.getAttribute("data-lang")] = cb.checked;
    }});
    return state;
  }}

  function langEnabledForSite(siteId, langs) {{
    const code = siteLangMap[siteId];
    if (!code) return true;
    return langs[code] !== false;
  }}

  function applyLanguages(state) {{
    const langs = state || currentLangState();
    document.querySelectorAll("#lang-toggles input[data-lang]").forEach(function (cb) {{
      const code = cb.getAttribute("data-lang");
      cb.checked = langs[code] !== false;
    }});
    document.querySelectorAll("#site-toggles .site-toggle[data-lang]").forEach(function (el) {{
      const code = el.getAttribute("data-lang");
      el.classList.toggle("hidden-lang", langs[code] === false);
    }});
    applySites(currentSiteState());
  }}

  function applySites(state) {{
    const langs = currentLangState();
    document.querySelectorAll("section.site").forEach(function (sec) {{
      const id = sec.getAttribute("data-site");
      const langOn = langEnabledForSite(id, langs);
      sec.classList.toggle("hidden", state[id] === false || !langOn);
    }});
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{
      cb.checked = state[cb.getAttribute("data-site")] !== false;
    }});
    document.querySelectorAll("#all-new li.headline[data-site]").forEach(function (li) {{
      const id = li.getAttribute("data-site");
      const lang = li.getAttribute("data-lang");
      li.classList.toggle("hidden-site", state[id] === false);
      li.classList.toggle("hidden-lang", lang && langs[lang] === false);
    }});
    refreshAllNewCount();
  }}

  function currentCollapsedState() {{
    const state = {{}};
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      state[sec.getAttribute("data-site")] = sec.classList.contains("collapsed");
    }});
    const allNew = document.getElementById("all-new");
    if (allNew) {{
      state["all-new"] = allNew.classList.contains("collapsed");
    }}
    const clusters = document.getElementById("clusters");
    if (clusters) {{
      state["clusters"] = clusters.classList.contains("collapsed");
    }}
    return state;
  }}

  function applyCollapsedSites(state) {{
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      const id = sec.getAttribute("data-site");
      const collapsed = state[id] === true;
      sec.classList.toggle("collapsed", collapsed);
      const btn = sec.querySelector(".site-collapse-toggle");
      if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }});
    const allNew = document.getElementById("all-new");
    if (allNew) {{
      const collapsed = state["all-new"] === true;
      allNew.classList.toggle("collapsed", collapsed);
      const btn = allNew.querySelector(".site-collapse-toggle");
      if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }}
    const clusters = document.getElementById("clusters");
    if (clusters) {{
      const collapsed = state["clusters"] === true;
      clusters.classList.toggle("collapsed", collapsed);
      const btn = clusters.querySelector(".site-collapse-toggle");
      if (btn) btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    }}
  }}

  document.querySelectorAll(
    "section.site .site-collapse-toggle, section.all-new .site-collapse-toggle, section.clusters .site-collapse-toggle"
  ).forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      const sec = btn.closest("section.site, section.all-new, section.clusters");
      if (!sec) return;
      const collapsed = !sec.classList.contains("collapsed");
      sec.classList.toggle("collapsed", collapsed);
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
      saveState(COLLAPSED_KEY, currentCollapsedState());
    }});
  }});

  function refreshAllNewCount() {{
    const section = document.getElementById("all-new");
    if (!section) return;
    const items = section.querySelectorAll("li.headline");
    let visible = 0;
    items.forEach(function (li) {{
      if (
        !li.classList.contains("hidden-site") &&
        !li.classList.contains("hidden-lang") &&
        !li.classList.contains("hidden-cat") &&
        !li.classList.contains("hidden-external") &&
        !li.classList.contains("hidden-exclude")
      ) visible += 1;
    }});
    const el = document.getElementById("all-new-count");
    if (el) el.textContent = "(" + visible + ")";
    const clusters = document.getElementById("clusters");
    if (clusters && !clusters.classList.contains("hidden")) rebuildClusters();
  }}

  function refreshCollapsedNewCounts() {{
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      const countEl = sec.querySelector(".collapsed-new-count");
      let n = 0;
      sec.querySelectorAll(".site-body li.headline[data-new='1']").forEach(function (li) {{
        if (isFilterVisible(li)) n += 1;
      }});
      if (n <= 0) {{
        if (countEl) countEl.remove();
      }} else {{
        const label = n === 1 ? "1 new article" : n + " new articles";
        if (countEl) countEl.textContent = label;
        else {{
          const span = document.createElement("span");
          span.className = "collapsed-new-count";
          span.textContent = label;
          const title = sec.querySelector("h2.site-title");
          if (title) title.appendChild(span);
        }}
      }}
    }});
  }}

  function applyAllNew(show) {{
    const cb = document.getElementById("toggle-all-new");
    if (cb) cb.checked = !!show;
    const section = document.getElementById("all-new");
    if (section) section.classList.toggle("hidden", !show);
  }}

  const CLUSTER_IGNORE_WORDS = {ignore_words_literal};

  const CLUSTER_ALIASES = {{
    rusland: "russia", russia: "russia", russian: "russia", russisk: "russia",
    russiske: "russia", russerne: "russia", russern: "russia", russere: "russia",
    ukraine: "ukraine", ukraina: "ukraine", ukrainsk: "ukraine", ukrainske: "ukraine",
    trump: "trump", trumps: "trump",
    putin: "putin", putins: "putin",
    gronland: "greenland", groenland: "greenland", grønland: "greenland",
    greenland: "greenland",
    klima: "climate", climate: "climate", klimat: "climate",
    israel: "israel", gaza: "gaza", hamas: "hamas",
    kina: "china", china: "china", chinese: "china", kinesisk: "china", kinesiske: "china",
    usa: "usa", amerika: "usa", america: "usa", american: "usa", amerikansk: "usa",
    colombia: "colombia", columbia: "colombia", kolumbien: "colombia",
    jordskaelv: "earthquake", jordskælv: "earthquake", earthquake: "earthquake",
    zelenskyj: "zelensky", zelensky: "zelensky", zelenskij: "zelensky"
  }};

  function clusterPathText(href) {{
    if (!href) return "";
    try {{
      const u = new URL(href, window.location.href);
      return decodeURIComponent(u.pathname || "").replace(/[\\/_+.-]+/g, " ");
    }} catch (e) {{
      return String(href).replace(/[\\/?&#=_+.-]+/g, " ");
    }}
  }}

  function foldClusterToken(w) {{
    return String(w || "")
      .toLowerCase()
      .replace(/æ/g, "ae")
      .replace(/ø/g, "oe")
      .replace(/å/g, "aa")
      .replace(/ä/g, "ae")
      .replace(/ö/g, "oe")
      .replace(/ü/g, "ue");
  }}

  function hasDanishLetters(w) {{
    return /[æøåäöü]/i.test(w || "");
  }}

  function rememberClusterDisplay(displayMap, canon, surface) {{
    if (!displayMap || !canon || !surface) return;
    const s = String(surface).toLowerCase();
    const prev = displayMap[canon];
    if (!prev) {{
      displayMap[canon] = s;
      return;
    }}
    const sDa = hasDanishLetters(s);
    const pDa = hasDanishLetters(prev);
    if (sDa && !pDa) displayMap[canon] = s;
    else if (sDa === pDa && s.length > prev.length) displayMap[canon] = s;
  }}

  function extractClusterTokens(title, href, displayMap) {{
    const raw = ((title || "") + " " + clusterPathText(href)).toLowerCase();
    const parts = raw.match(/[a-z0-9æøåäöü]+/gi) || [];
    const seen = {{}};
    const out = [];
    parts.forEach(function (p) {{
      const original = p.toLowerCase();
      let w = original;
      if (w.length < 3) return;
      if (/^\\d+$/.test(w)) return;
      if (/^(art|ece|cid|id)\\d+$/i.test(w)) return;
      if (/^\\d{{4,}}$/.test(w)) return;
      const folded = foldClusterToken(w);
      if (CLUSTER_IGNORE_WORDS[w] || CLUSTER_IGNORE_WORDS[folded]) return;
      let canon;
      if (CLUSTER_ALIASES[w]) canon = CLUSTER_ALIASES[w];
      else if (CLUSTER_ALIASES[folded]) canon = CLUSTER_ALIASES[folded];
      else canon = folded;
      if (CLUSTER_IGNORE_WORDS[canon]) return;
      if (canon.length < 3) return;
      rememberClusterDisplay(displayMap, canon, original);
      if (seen[canon]) return;
      seen[canon] = true;
      out.push(canon);
    }});
    return out;
  }}

  function titleCaseClusterLabel(word, displayMap) {{
    if (!word) return "";
    // Prefer Danish spellings for known topics
    const labels = {{
      russia: "Rusland",
      ukraine: "Ukraine",
      trump: "Trump",
      putin: "Putin",
      greenland: "Grønland",
      climate: "Klima",
      colombia: "Colombia",
      earthquake: "Jordskælv",
      zelensky: "Zelenskyj",
      china: "Kina",
      usa: "USA",
      israel: "Israel",
      gaza: "Gaza",
      hamas: "Hamas"
    }};
    if (labels[word]) return labels[word];
    const surface = displayMap && displayMap[word] ? displayMap[word] : word;
    return surface.charAt(0).toUpperCase() + surface.slice(1);
  }}

  function isClusterSourceVisible(li) {{
    if (
      li.classList.contains("hidden-cat") ||
      li.classList.contains("hidden-external") ||
      li.classList.contains("hidden-exclude")
    ) return false;
    const sec = li.closest("section.site");
    if (sec && sec.classList.contains("hidden")) return false;
    return true;
  }}

  function ensureClusterSourceBadge(li, siteId, siteName) {{
    if (!li || li.querySelector(".src-badge")) return;
    const badge = document.createElement("span");
    badge.className = "src-badge";
    badge.title = siteName || siteId || "";
    const name = document.createElement("span");
    name.className = "src-name";
    name.textContent = siteName || siteId || "";
    badge.appendChild(name);
    li.insertBefore(badge, li.firstChild);
  }}

  function tokenInTitle(token, title) {{
    const foldedTitle = foldClusterToken(title || "");
    if (foldedTitle.indexOf(token) !== -1) return true;
    for (const [alias, canon] of Object.entries(CLUSTER_ALIASES)) {{
      if (canon === token && foldedTitle.indexOf(foldClusterToken(alias)) !== -1) return true;
    }}
    return false;
  }}

  function currentClusterMinSize() {{
    const sec = document.getElementById("clusters");
    let n = sec ? Number(sec.getAttribute("data-cluster-min-size")) : initialClusterMinSize;
    if (!Number.isFinite(n)) n = initialClusterMinSize || 2;
    return Math.max(1, Math.min(10, Math.round(n)));
  }}

  function applyClusterMinSize(value, rebuild) {{
    const n = Math.max(1, Math.min(10, Math.round(Number(value) || 2)));
    const sec = document.getElementById("clusters");
    if (sec) sec.setAttribute("data-cluster-min-size", String(n));
    const label = document.getElementById("cluster-min-size-value");
    if (label) label.textContent = String(n);
    const dec = document.getElementById("btn-cluster-min-dec");
    const inc = document.getElementById("btn-cluster-min-inc");
    if (dec) dec.disabled = n <= 1;
    if (inc) inc.disabled = n >= 10;
    if (rebuild !== false) {{
      const clusters = document.getElementById("clusters");
      if (clusters && !clusters.classList.contains("hidden")) rebuildClusters();
    }}
    return n;
  }}

  let clusterState = null;
  let clusterGraph = null;

  function currentClusterView() {{
    const sec = document.getElementById("clusters");
    const mode = sec && sec.getAttribute("data-cluster-view");
    return mode === "graph" ? "graph" : "list";
  }}

  function applyClusterView(mode) {{
    const view = mode === "graph" ? "graph" : "list";
    const sec = document.getElementById("clusters");
    if (sec) sec.setAttribute("data-cluster-view", view);
    const listBtn = document.getElementById("btn-cluster-list");
    const graphBtn = document.getElementById("btn-cluster-graph");
    if (listBtn) listBtn.setAttribute("aria-pressed", view === "list" ? "true" : "false");
    if (graphBtn) graphBtn.setAttribute("aria-pressed", view === "graph" ? "true" : "false");
    const listView = document.getElementById("clusters-list-view");
    const graphView = document.getElementById("clusters-graph-view");
    if (listView) listView.classList.toggle("hidden", view !== "list");
    if (graphView) graphView.classList.toggle("hidden", view !== "graph");
    if (view === "list") stopClusterGraph();
    if (clusterState) {{
      if (view === "graph") renderClusterGraph(clusterState);
      else renderClusterList(clusterState);
    }} else {{
      const sec2 = document.getElementById("clusters");
      if (sec2 && !sec2.classList.contains("hidden")) rebuildClusters();
    }}
  }}

  function computeClusterState() {{
    const onlyNew = document.body.classList.contains("only-new");
    const siteNameById = {{}};
    document.querySelectorAll("section.site[data-site]").forEach(function (sec) {{
      const id = sec.getAttribute("data-site");
      const nameEl = sec.querySelector(".site-name");
      siteNameById[id] = nameEl ? nameEl.textContent.trim() : id;
    }});

    const sourceItems = [];
    const seenHref = {{}};
    const displayMap = {{}};
    document.querySelectorAll("section.site li.headline[data-href]").forEach(function (li) {{
      if (!isClusterSourceVisible(li)) return;
      if (onlyNew && li.getAttribute("data-new") !== "1") return;
      const href = li.getAttribute("data-href");
      if (!href || seenHref[href]) return;
      seenHref[href] = true;
      const a = li.querySelector("a");
      const title = a ? a.textContent : "";
      const tip = li.querySelector(".url-tip");
      const tipText = tip ? tip.textContent : "";
      const siteId = li.getAttribute("data-site") || "";
      const toks = extractClusterTokens(title + " " + tipText, href, displayMap);
      sourceItems.push({{
        li: li,
        href: href,
        title: title,
        siteId: siteId,
        siteName: siteNameById[siteId] || siteId,
        tokens: toks,
        titleTokens: extractClusterTokens(title, "", displayMap),
      }});
    }});

    const df = {{}};
    sourceItems.forEach(function (item) {{
      item.tokens.forEach(function (t) {{
        df[t] = (df[t] || 0) + 1;
      }});
    }});

    const maxDf = Math.max(8, Math.min(30, Math.floor(sourceItems.length * 0.08) || 8));
    const aliasCanon = {{}};
    Object.keys(CLUSTER_ALIASES).forEach(function (k) {{
      aliasCanon[CLUSTER_ALIASES[k]] = true;
    }});
    const groups = {{}};
    Object.keys(df).forEach(function (token) {{
      const n = df[token];
      if (n < 2 || n > maxDf) return;
      if (token.length < 5 && !aliasCanon[token]) return;
      const members = sourceItems.filter(function (item) {{
        return item.tokens.indexOf(token) !== -1;
      }});
      if (members.length < 2) return;
      const titleHits = members.filter(function (item) {{
        return tokenInTitle(token, item.title) || (item.titleTokens && item.titleTokens.indexOf(token) !== -1);
      }}).length;
      if (titleHits < 2 && !(titleHits >= 1 && members.length >= 3)) return;
      groups[token] = {{ items: members, titleHits: titleHits }};
    }});

    const minSize = currentClusterMinSize();
    const keys = Object.keys(groups).filter(function (token) {{
      return groups[token].items.length >= minSize;
    }});
    keys.sort(function (a, b) {{
      const ga = groups[a];
      const gb = groups[b];
      if (gb.titleHits > 0 !== ga.titleHits > 0) return gb.titleHits > 0 ? -1 : 1;
      const diff = gb.items.length - ga.items.length;
      if (diff) return diff;
      return a.localeCompare(b);
    }});
    if (keys.length > 60) keys.length = 60;

    const hrefIndex = {{}};
    keys.forEach(function (key) {{
      groups[key].items.forEach(function (item) {{
        if (!hrefIndex[item.href]) hrefIndex[item.href] = [];
        hrefIndex[item.href].push(key);
      }});
    }});
    const edgeMap = {{}};
    Object.keys(hrefIndex).forEach(function (href) {{
      const ks = hrefIndex[href];
      if (ks.length < 2) return;
      for (let i = 0; i < ks.length; i++) {{
        for (let j = i + 1; j < ks.length; j++) {{
          const a = ks[i] < ks[j] ? ks[i] : ks[j];
          const b = ks[i] < ks[j] ? ks[j] : ks[i];
          const ek = a + "|" + b;
          if (!edgeMap[ek]) edgeMap[ek] = {{ source: a, target: b, weight: 1 }};
          else edgeMap[ek].weight += 1;
        }}
      }}
    }});
    const edges = Object.keys(edgeMap).map(function (k) {{ return edgeMap[k]; }});

    return {{
      keys: keys,
      groups: groups,
      displayMap: displayMap,
      sourceCount: sourceItems.length,
      edges: edges,
    }};
  }}

  function rebuildClusters() {{
    const countEl = document.getElementById("clusters-count");
    const data = computeClusterState();
    clusterState = data;
    if (!data.keys.length) {{
      if (countEl) countEl.textContent = "(0)";
      stopClusterGraph();
      const inner = document.getElementById("clusters-inner");
      if (inner) {{
        inner.innerHTML = "";
        const p = document.createElement("p");
        p.className = "empty";
        p.textContent = data.sourceCount
          ? "No shared topics among current headlines."
          : "No headlines available to cluster.";
        inner.appendChild(p);
      }}
      resetClusterGraphDetail();
      return;
    }}
    const seenInAny = {{}};
    data.keys.forEach(function (key) {{
      data.groups[key].items.forEach(function (item) {{
        seenInAny[item.href] = true;
      }});
    }});
    if (countEl) {{
      countEl.textContent =
        "(" + data.keys.length + " · " + Object.keys(seenInAny).length + ")";
    }}
    if (currentClusterView() === "graph") renderClusterGraph(data);
    else renderClusterList(data);
  }}

  function renderClusterList(data) {{
    stopClusterGraph();
    const inner = document.getElementById("clusters-inner");
    if (!inner) return;
    inner.innerHTML = "";
    data.keys.forEach(function (key) {{
      const items = data.groups[key].items;
      const group = document.createElement("div");
      group.className = "cluster-group";
      group.setAttribute("data-cluster", key);
      const label = document.createElement("h3");
      label.className = "cluster-label";
      label.textContent = titleCaseClusterLabel(key, data.displayMap) + " ";
      const size = document.createElement("span");
      size.className = "cluster-size";
      size.textContent = "(" + items.length + ")";
      label.appendChild(size);
      group.appendChild(label);
      const ol = document.createElement("ol");
      items.forEach(function (item) {{
        const clone = item.li.cloneNode(true);
        clone.classList.remove("hidden-seen-limit");
        ensureClusterSourceBadge(clone, item.siteId, item.siteName);
        ol.appendChild(clone);
      }});
      group.appendChild(ol);
      inner.appendChild(group);
    }});
  }}

  function resetClusterGraphDetail(message) {{
    const title = document.getElementById("clusters-graph-detail-title");
    const list = document.getElementById("clusters-graph-articles");
    if (title) title.textContent = "Select a cluster";
    if (list) {{
      list.innerHTML = "";
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = message || "Click a cluster node to see articles.";
      list.appendChild(li);
    }}
  }}

  function showClusterGraphDetail(key, data) {{
    const group = data.groups[key];
    if (!group) return;
    const title = document.getElementById("clusters-graph-detail-title");
    const list = document.getElementById("clusters-graph-articles");
    if (title) {{
      title.textContent =
        titleCaseClusterLabel(key, data.displayMap) + " (" + group.items.length + ")";
    }}
    if (!list) return;
    list.innerHTML = "";
    group.items.forEach(function (item) {{
      const clone = item.li.cloneNode(true);
      clone.classList.remove("hidden-seen-limit");
      ensureClusterSourceBadge(clone, item.siteId, item.siteName);
      list.appendChild(clone);
    }});
  }}

  function stopClusterGraph() {{
    if (clusterGraph && clusterGraph.raf) {{
      cancelAnimationFrame(clusterGraph.raf);
      clusterGraph.raf = 0;
    }}
    if (clusterGraph && clusterGraph.cleanup) clusterGraph.cleanup();
    clusterGraph = null;
  }}

  function cssVar(name, fallback) {{
    const v = getComputedStyle(document.body).getPropertyValue(name);
    return (v && v.trim()) || fallback;
  }}

  function renderClusterGraph(data) {{
    stopClusterGraph();
    const canvas = document.getElementById("clusters-graph-canvas");
    if (!canvas || !data.keys.length) {{
      resetClusterGraphDetail(
        data && data.sourceCount
          ? "No shared topics among current headlines."
          : "No headlines available to cluster."
      );
      return;
    }}
    resetClusterGraphDetail();

    const dpr = window.devicePixelRatio || 1;
    function resize() {{
      const rect = canvas.getBoundingClientRect();
      const w = Math.max(320, Math.floor(rect.width));
      const h = Math.max(280, Math.floor(rect.height));
      canvas.width = Math.floor(w * dpr);
      canvas.height = Math.floor(h * dpr);
      return {{ w: w, h: h }};
    }}
    let size = resize();

    const maxCount = data.keys.reduce(function (m, key) {{
      return Math.max(m, data.groups[key].items.length);
    }}, 1);
    const rMax = Math.min(44, Math.min(size.w, size.h) * 0.09);
    const rMin = 8;
    const nodes = data.keys.map(function (key, i) {{
      const n = data.groups[key].items.length;
      const angle = (i / data.keys.length) * Math.PI * 2;
      const radius = Math.min(size.w, size.h) * 0.28;
      const t = n / maxCount;
      return {{
        id: key,
        label: titleCaseClusterLabel(key, data.displayMap),
        count: n,
        x: size.w / 2 + Math.cos(angle) * radius,
        y: size.h / 2 + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        // Emphasize large clusters: r = rMin + (rMax-rMin) * (size/max)^2
        r: rMin + (rMax - rMin) * (t * t),
      }};
    }});
    const nodeById = {{}};
    nodes.forEach(function (n) {{ nodeById[n.id] = n; }});
    const links = data.edges
      .map(function (e) {{
        return {{
          source: nodeById[e.source],
          target: nodeById[e.target],
          weight: e.weight || 1,
        }};
      }})
      .filter(function (e) {{ return e.source && e.target; }});

    let selectedId = null;
    let dragging = null;
    let moved = false;

    function tick() {{
      const w = size.w;
      const h = size.h;
      // Repulsion
      for (let i = 0; i < nodes.length; i++) {{
        for (let j = i + 1; j < nodes.length; j++) {{
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let dist2 = dx * dx + dy * dy;
          if (dist2 < 1) dist2 = 1;
          const dist = Math.sqrt(dist2);
          const force = 900 / dist2;
          dx = (dx / dist) * force;
          dy = (dy / dist) * force;
          a.vx += dx;
          a.vy += dy;
          b.vx -= dx;
          b.vy -= dy;
        }}
      }}
      // Attraction along shared-article edges
      links.forEach(function (link) {{
        const a = link.source;
        const b = link.target;
        let dx = b.x - a.x;
        let dy = b.y - a.y;
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy));
        const ideal = 70 + Math.min(80, link.weight * 8);
        const force = (dist - ideal) * 0.02 * Math.min(3, link.weight);
        dx = (dx / dist) * force;
        dy = (dy / dist) * force;
        a.vx += dx;
        a.vy += dy;
        b.vx -= dx;
        b.vy -= dy;
      }});
      // Center gravity + damping + bounds
      nodes.forEach(function (n) {{
        if (dragging && dragging.id === n.id) {{
          n.vx = 0;
          n.vy = 0;
          return;
        }}
        n.vx += (w / 2 - n.x) * 0.005;
        n.vy += (h / 2 - n.y) * 0.005;
        n.vx *= 0.85;
        n.vy *= 0.85;
        n.x += n.vx;
        n.y += n.vy;
        const m = n.r + 4;
        if (n.x < m) {{ n.x = m; n.vx *= -0.4; }}
        if (n.y < m) {{ n.y = m; n.vy *= -0.4; }}
        if (n.x > w - m) {{ n.x = w - m; n.vx *= -0.4; }}
        if (n.y > h - m) {{ n.y = h - m; n.vy *= -0.4; }}
      }});
    }}

    function draw() {{
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.w, size.h);
      const edgeColor = cssVar("--border", "#ddd");
      const textColor = cssVar("--text", "#1a1a1a");
      const accent = cssVar("--accent", "#1565c0");
      const panel = cssVar("--panel", "#fff");
      const muted = cssVar("--muted", "#666");

      ctx.lineWidth = 1;
      links.forEach(function (link) {{
        ctx.strokeStyle = edgeColor;
        ctx.globalAlpha = Math.min(0.85, 0.25 + link.weight * 0.12);
        ctx.beginPath();
        ctx.moveTo(link.source.x, link.source.y);
        ctx.lineTo(link.target.x, link.target.y);
        ctx.stroke();
      }});
      ctx.globalAlpha = 1;

      nodes.forEach(function (n) {{
        const active = n.id === selectedId;
        ctx.beginPath();
        ctx.fillStyle = active ? accent : panel;
        ctx.strokeStyle = accent;
        ctx.lineWidth = active ? 2.5 : 1.5;
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();

        ctx.fillStyle = active ? "#fff" : textColor;
        const fontPx = Math.max(10, Math.min(15, Math.round(n.r * 0.55)));
        ctx.font = "600 " + fontPx + "px Lato, Helvetica, Arial, sans-serif";
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const maxChars = Math.max(6, Math.floor(n.r / 2.2));
        const label = n.label.length > maxChars ? n.label.slice(0, Math.max(4, maxChars - 1)) + "…" : n.label;
        ctx.fillText(label, n.x, n.y - 1);
        ctx.fillStyle = active ? "rgba(255,255,255,0.85)" : muted;
        ctx.font = "11px Lato, Helvetica, Arial, sans-serif";
        ctx.fillText(String(n.count), n.x, n.y + n.r + 11);
      }});
    }}

    function frame() {{
      for (let i = 0; i < 2; i++) tick();
      draw();
      clusterGraph.raf = requestAnimationFrame(frame);
    }}

    function canvasPos(evt) {{
      const rect = canvas.getBoundingClientRect();
      return {{
        x: ((evt.clientX - rect.left) / rect.width) * size.w,
        y: ((evt.clientY - rect.top) / rect.height) * size.h,
      }};
    }}

    function hitTest(pos) {{
      for (let i = nodes.length - 1; i >= 0; i--) {{
        const n = nodes[i];
        const dx = pos.x - n.x;
        const dy = pos.y - n.y;
        if (dx * dx + dy * dy <= (n.r + 4) * (n.r + 4)) return n;
      }}
      return null;
    }}

    function onPointerDown(evt) {{
      const pos = canvasPos(evt);
      const hit = hitTest(pos);
      if (!hit) return;
      dragging = hit;
      moved = false;
      canvas.classList.add("is-dragging");
      canvas.setPointerCapture(evt.pointerId);
    }}
    function onPointerMove(evt) {{
      if (!dragging) return;
      const pos = canvasPos(evt);
      dragging.x = pos.x;
      dragging.y = pos.y;
      dragging.vx = 0;
      dragging.vy = 0;
      moved = true;
    }}
    function onPointerUp(evt) {{
      if (!dragging) return;
      const node = dragging;
      dragging = null;
      canvas.classList.remove("is-dragging");
      try {{ canvas.releasePointerCapture(evt.pointerId); }} catch (e) {{}}
      if (!moved) {{
        selectedId = node.id;
        showClusterGraphDetail(node.id, data);
      }}
    }}
    function onResize() {{
      const prev = size;
      size = resize();
      const sx = size.w / Math.max(1, prev.w);
      const sy = size.h / Math.max(1, prev.h);
      nodes.forEach(function (n) {{
        n.x *= sx;
        n.y *= sy;
      }});
    }}

    canvas.addEventListener("pointerdown", onPointerDown);
    canvas.addEventListener("pointermove", onPointerMove);
    canvas.addEventListener("pointerup", onPointerUp);
    canvas.addEventListener("pointercancel", onPointerUp);
    window.addEventListener("resize", onResize);

    clusterGraph = {{
      raf: 0,
      cleanup: function () {{
        canvas.removeEventListener("pointerdown", onPointerDown);
        canvas.removeEventListener("pointermove", onPointerMove);
        canvas.removeEventListener("pointerup", onPointerUp);
        canvas.removeEventListener("pointercancel", onPointerUp);
        window.removeEventListener("resize", onResize);
        canvas.classList.remove("is-dragging");
      }},
    }};
    clusterGraph.raf = requestAnimationFrame(frame);
  }}

  function applyClusters(show) {{
    const on = !!show;
    const cb = document.getElementById("toggle-clusters");
    if (cb) cb.checked = on;
    const btn = document.getElementById("btn-cluster");
    if (btn) btn.setAttribute("aria-pressed", on ? "true" : "false");
    const section = document.getElementById("clusters");
    if (section) section.classList.toggle("hidden", !on);
    if (on) rebuildClusters();
    else stopClusterGraph();
  }}

  function applyDimOpened(dim) {{
    const cb = document.getElementById("toggle-dim-opened");
    if (cb) cb.checked = !!dim;
    document.body.classList.toggle("dim-opened", !!dim);
  }}

  function applyOpenedTodayPanel(show) {{
    const cb = document.getElementById("toggle-opened-today");
    if (cb) cb.checked = !!show;
    const section = document.getElementById("opened-today");
    if (section) section.classList.toggle("hidden", !show);
  }}

  function applyDarkMode(dark) {{
    const on = !!dark;
    document.body.classList.toggle("dark", on);
    const cb = document.getElementById("toggle-dark");
    if (cb) cb.checked = on;
    const btn = document.getElementById("btn-theme");
    if (btn) {{
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.textContent = on ? "Light" : "Dark";
    }}
  }}

  function loadOpenedLinks() {{
    try {{
      const raw = localStorage.getItem(OPENED_LINKS_KEY);
      if (!raw) return {{}};
      const data = JSON.parse(raw);
      return data && typeof data === "object" ? data : {{}};
    }} catch (e) {{
      return {{}};
    }}
  }}

  function saveOpenedLinks(map) {{
    localStorage.setItem(OPENED_LINKS_KEY, JSON.stringify(map));
  }}

  function pruneOpenedLinks(map, maxAgeDays) {{
    const cutoff = Date.now() - maxAgeDays * 24 * 60 * 60 * 1000;
    const next = {{}};
    Object.keys(map).forEach(function (href) {{
      const ts = Date.parse(map[href]);
      if (!isNaN(ts) && ts >= cutoff) next[href] = map[href];
    }});
    return next;
  }}

  function sameLocalDay(iso) {{
    const ts = Date.parse(iso);
    if (isNaN(ts)) return false;
    const d = new Date(ts);
    const now = new Date();
    return (
      d.getFullYear() === now.getFullYear() &&
      d.getMonth() === now.getMonth() &&
      d.getDate() === now.getDate()
    );
  }}

  function applyOpenedMarks(map) {{
    document.querySelectorAll("li.headline[data-href]").forEach(function (li) {{
      const href = li.getAttribute("data-href");
      li.classList.toggle("opened", !!(href && map[href]));
    }});
  }}

  function rebuildOpenedToday(map) {{
    const list = document.getElementById("opened-today-list");
    const countEl = document.getElementById("opened-today-count");
    if (!list) return;
    const today = [];
    Object.keys(map).forEach(function (href) {{
      if (sameLocalDay(map[href])) today.push({{ href: href, at: map[href] }});
    }});
    today.sort(function (a, b) {{
      return Date.parse(b.at) - Date.parse(a.at);
    }});
    list.innerHTML = "";
    if (!today.length) {{
      list.innerHTML = '<li class="empty">No articles opened today.</li>';
      if (countEl) countEl.textContent = "(0)";
      return;
    }}
    today.forEach(function (item) {{
      let title = item.href;
      let match = null;
      document.querySelectorAll("li.headline[data-href]").forEach(function (li) {{
        if (!match && li.getAttribute("data-href") === item.href) {{
          const a = li.querySelector("a");
          if (a) match = a;
        }}
      }});
      if (match && match.textContent) title = match.textContent;
      const li = document.createElement("li");
      li.className = "headline opened";
      li.setAttribute("data-href", item.href);
      const main = document.createElement("span");
      main.className = "hl-main";
      const a = document.createElement("a");
      a.href = item.href;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = title;
      main.appendChild(a);
      li.appendChild(main);
      list.appendChild(li);
    }});
    if (countEl) countEl.textContent = "(" + today.length + ")";
  }}

  function markOpened(href) {{
    if (!href) return;
    let map = loadOpenedLinks();
    map[href] = new Date().toISOString();
    map = pruneOpenedLinks(map, 30);
    saveOpenedLinks(map);
    applyOpenedMarks(map);
    rebuildOpenedToday(map);
  }}

  function clearAllBrowserData() {{
    const keys = [];
    for (let i = 0; i < localStorage.length; i++) {{
      const k = localStorage.key(i);
      if (k && k.indexOf("newsextract.") === 0) keys.push(k);
    }}
    keys.forEach(function (k) {{ localStorage.removeItem(k); }});
    try {{
      const cookies = document.cookie ? document.cookie.split(";") : [];
      cookies.forEach(function (c) {{
        const name = c.split("=")[0].trim();
        if (!name) return;
        document.cookie = name + "=;expires=Thu, 01 Jan 1970 00:00:00 GMT;path=/";
      }});
    }} catch (e) {{}}
  }}

  function applyCats(state) {{
    document.querySelectorAll("li.headline[data-category]").forEach(function (li) {{
      const id = li.getAttribute("data-category");
      li.classList.toggle("hidden-cat", state[id] === false);
    }});
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{
      cb.checked = state[cb.getAttribute("data-category")] !== false;
    }});
    applySeenLimit(currentSeenLimit());
    refreshCollapsedNewCounts();
    refreshAllNewCount();
  }}

  function applyExternal(show) {{
    document.getElementById("toggle-external").checked = !!show;
    document.querySelectorAll("li.headline[data-external='1']").forEach(function (li) {{
      li.classList.toggle("hidden-external", !show);
    }});
    applySeenLimit(currentSeenLimit());
    refreshCollapsedNewCounts();
    refreshAllNewCount();
  }}

  function applyOnlyNew(only) {{
    document.getElementById("toggle-only-new").checked = !!only;
    document.body.classList.toggle("only-new", !!only);
    const clusters = document.getElementById("clusters");
    if (clusters && !clusters.classList.contains("hidden")) rebuildClusters();
  }}

  function applyBgStrength(value) {{
    const n = Math.max(0, Math.min(100, Number(value) || 0));
    document.documentElement.style.setProperty("--hl-bg-strength", n + "%");
    const slider = document.getElementById("slider-bg-strength");
    const label = document.getElementById("slider-bg-strength-value");
    if (slider) slider.value = String(n);
    if (label) label.textContent = n + "%";
    return n;
  }}

  function currentBgStrength() {{
    return Number(document.getElementById("slider-bg-strength").value) || 0;
  }}

  function isFilterVisible(li) {{
    return (
      !li.classList.contains("hidden-cat") &&
      !li.classList.contains("hidden-external") &&
      !li.classList.contains("hidden-exclude")
    );
  }}

  function applySeenLimit(value) {{
    const n = Math.max(0, Math.min(500, Number(value) || 0));
    const input = document.getElementById("input-seen-limit");
    const label = document.getElementById("input-seen-limit-value");
    if (input) input.value = String(n);
    if (label) label.textContent = String(n);
    document.querySelectorAll(".freshness-block[data-freshness='seen']").forEach(function (block) {{
      const items = Array.prototype.slice.call(
        block.querySelectorAll("li.headline[data-seen-index]")
      );
      items.forEach(function (li) {{
        li.classList.remove("hidden-seen-limit");
      }});
      const visible = items.filter(isFilterVisible);
      const matching = visible.length;
      const pages = n > 0 ? Math.max(1, Math.ceil(matching / n)) : 1;
      let page = Number(block.getAttribute("data-seen-page"));
      if (!Number.isFinite(page) || page < 0) page = 0;
      if (page > pages - 1) page = pages - 1;
      block.setAttribute("data-seen-page", String(page));

      const start = n > 0 ? page * n : 0;
      const end = n > 0 ? start + n : 0;
      visible.forEach(function (li, i) {{
        li.classList.toggle("hidden-seen-limit", i < start || i >= end);
      }});
      const shown = n > 0 ? Math.max(0, Math.min(end, matching) - start) : 0;
      const shownEl = block.querySelector(".seen-shown");
      const totalEl = block.querySelector(".seen-total");
      if (shownEl) shownEl.textContent = String(shown);
      if (totalEl) totalEl.textContent = String(matching);
      block.classList.toggle("hidden-seen-limit-block", n === 0 || matching === 0);

      const hasPages = n > 0 && matching > n;
      block.classList.toggle("has-seen-pages", hasPages);
      const prevBtn = block.querySelector(".seen-prev");
      const nextBtn = block.querySelector(".seen-next");
      const pageLabel = block.querySelector(".seen-page-label");
      if (prevBtn) {{
        prevBtn.textContent = "Previous " + n;
        prevBtn.disabled = !hasPages || page <= 0;
      }}
      if (nextBtn) {{
        nextBtn.textContent = "Next " + n;
        nextBtn.disabled = !hasPages || page >= pages - 1;
      }}
      if (pageLabel) pageLabel.textContent = "(" + (page + 1) + " of " + pages + ")";

      const list = block.querySelector("ol");
      if (list) {{
        const padNeeded = hasPages && shown > 0 && shown < n ? n - shown : 0;
        let pads = list.querySelectorAll("li.seen-pad");
        while (pads.length > padNeeded) {{
          pads[pads.length - 1].remove();
          pads = list.querySelectorAll("li.seen-pad");
        }}
        while (pads.length < padNeeded) {{
          const pad = document.createElement("li");
          pad.className = "seen-pad";
          pad.setAttribute("aria-hidden", "true");
          pad.textContent = "\\u00a0";
          list.appendChild(pad);
          pads = list.querySelectorAll("li.seen-pad");
        }}
      }}
    }});
    return n;
  }}

  document.addEventListener("click", function (e) {{
    const btn = e.target.closest ? e.target.closest(".seen-prev, .seen-next") : null;
    if (!btn) return;
    const block = btn.closest(".freshness-block[data-freshness='seen']");
    if (!block) return;
    const n = currentSeenLimit();
    if (n <= 0) return;
    const matching = Array.prototype.slice
      .call(block.querySelectorAll("li.headline[data-seen-index]"))
      .filter(isFilterVisible).length;
    const pages = Math.max(1, Math.ceil(matching / n));
    let page = Number(block.getAttribute("data-seen-page")) || 0;
    if (btn.classList.contains("seen-prev")) page -= 1;
    else page += 1;
    if (page < 0) page = 0;
    if (page > pages - 1) page = pages - 1;
    block.setAttribute("data-seen-page", String(page));
    applySeenLimit(n);
  }});

  function currentSeenLimit() {{
    return Number(document.getElementById("input-seen-limit").value) || 0;
  }}

  function parseHighlightWords(raw) {{
    const seen = {{}};
    const words = [];
    String(raw || "").split(",").forEach(function (part) {{
      const w = part.replace(/\\s+/g, " ").trim().toLowerCase();
      if (w.length < 2 || seen[w]) return;
      seen[w] = true;
      words.push(w);
    }});
    return words;
  }}

  function currentHighlightWords() {{
    return document.getElementById("input-highlight-words").value || "";
  }}

  function applyHighlightWords(raw) {{
    const input = document.getElementById("input-highlight-words");
    if (input && input.value !== raw) input.value = raw;
    const keywords = parseHighlightWords(raw);
    document.querySelectorAll("li.headline").forEach(function (li) {{
      const a = li.querySelector("a");
      if (!a) return;
      const hay = (a.textContent || "").toLowerCase();
      const hit = keywords.some(function (k) {{ return hay.indexOf(k) !== -1; }});
      li.classList.toggle("hl-keyword", hit);
    }});
    return raw;
  }}

  function currentExcludeWords() {{
    return document.getElementById("input-exclude-words").value || "";
  }}

  function applyExcludeWords(raw) {{
    const input = document.getElementById("input-exclude-words");
    if (input && input.value !== raw) input.value = raw;
    const keywords = parseHighlightWords(raw);
    document.querySelectorAll("li.headline").forEach(function (li) {{
      const a = li.querySelector("a");
      if (!a) return;
      const hay = (a.textContent || "").toLowerCase();
      const hit = keywords.some(function (k) {{ return hay.indexOf(k) !== -1; }});
      li.classList.toggle("hidden-exclude", hit);
    }});
    applySeenLimit(currentSeenLimit());
    refreshCollapsedNewCounts();
    refreshAllNewCount();
    return raw;
  }}

  function setStatus(msg) {{
    document.getElementById("status").textContent = msg || "";
  }}

  function downloadJson(filename, obj) {{
    const blob = new Blob([JSON.stringify(obj, null, 2) + "\\n"], {{ type: "application/json" }});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = filename;
    a.click();
    URL.revokeObjectURL(a.href);
  }}

  applySites(loadState(SITE_KEY, initialSites));
  applyLanguages(loadState(LANG_KEY, initialLanguages));
  applyCats(loadState(CAT_KEY, initialCats));
  applyExternal(loadBool(EXT_KEY, initialExternal));
  applyOnlyNew(loadBool(ONLY_NEW_KEY, initialOnlyNew));
  applyAllNew(loadBool(ALL_NEW_KEY, initialAllNew));
  (function () {{
    let minSize = initialClusterMinSize;
    try {{
      const raw = localStorage.getItem("newsextract.clusterMinSize");
      if (raw !== null && raw !== "") minSize = Number(JSON.parse(raw));
    }} catch (e) {{}}
    applyClusterMinSize(minSize, false);
  }})();
  (function () {{
    let view = initialClusterView;
    try {{
      const raw = localStorage.getItem("newsextract.clusterView");
      if (raw !== null && raw !== "") {{
        const parsed = JSON.parse(raw);
        if (parsed === "list" || parsed === "graph") view = parsed;
      }}
    }} catch (e) {{}}
    const sec = document.getElementById("clusters");
    if (sec) sec.setAttribute("data-cluster-view", view);
    const listBtn = document.getElementById("btn-cluster-list");
    const graphBtn = document.getElementById("btn-cluster-graph");
    if (listBtn) listBtn.setAttribute("aria-pressed", view === "list" ? "true" : "false");
    if (graphBtn) graphBtn.setAttribute("aria-pressed", view === "graph" ? "true" : "false");
    const listView = document.getElementById("clusters-list-view");
    const graphView = document.getElementById("clusters-graph-view");
    if (listView) listView.classList.toggle("hidden", view !== "list");
    if (graphView) graphView.classList.toggle("hidden", view !== "graph");
  }})();
  applyClusters(loadBool(CLUSTERS_KEY, initialClusters));
  applyDimOpened(loadBool(DIM_OPENED_KEY, initialDimOpened));
  applyOpenedTodayPanel(loadBool(OPENED_TODAY_KEY, initialOpenedToday));
  applyDarkMode(loadBool(DARK_MODE_KEY, initialDarkMode));
  applyCollapsedSites(loadState(COLLAPSED_KEY, {{}}));
  requestAnimationFrame(function () {{
    document.documentElement.classList.add("collapse-ready");
  }});
  (function () {{
    let map = pruneOpenedLinks(loadOpenedLinks(), 30);
    saveOpenedLinks(map);
    applyOpenedMarks(map);
    rebuildOpenedToday(map);
  }})();
  (function () {{
    let strength = initialBgStrength;
    try {{
      const raw = localStorage.getItem("newsextract.bgStrength");
      if (raw !== null && raw !== "") strength = Number(JSON.parse(raw));
    }} catch (e) {{}}
    applyBgStrength(strength);
  }})();
  (function () {{
    let limit = initialSeenLimit;
    try {{
      const raw = localStorage.getItem("newsextract.seenLimit");
      if (raw !== null && raw !== "") limit = Number(JSON.parse(raw));
    }} catch (e) {{}}
    applySeenLimit(limit);
  }})();
  (function () {{
    let words = initialHighlightWords;
    try {{
      const raw = localStorage.getItem("newsextract.highlightWords");
      if (raw !== null && raw !== "") words = JSON.parse(raw);
    }} catch (e) {{}}
    applyHighlightWords(typeof words === "string" ? words : "");
  }})();
  (function () {{
    let words = initialExcludeWords;
    try {{
      const raw = localStorage.getItem("newsextract.excludeWords");
      if (raw !== null && raw !== "") words = JSON.parse(raw);
    }} catch (e) {{}}
    applyExcludeWords(typeof words === "string" ? words : "");
  }})();
  (function () {{
    let order = initialSiteOrder;
    try {{
      const raw = localStorage.getItem("newsextract.siteOrder");
      if (raw !== null && raw !== "") {{
        const parsed = JSON.parse(raw);
        if (Array.isArray(parsed) && parsed.length) order = parsed;
      }}
    }} catch (e) {{}}
    applySiteOrder(order);
  }})();

  (function setupSiteDragDrop() {{
    const root = document.getElementById("site-toggles");
    let dragEl = null;

    function rowFromEvent(e) {{
      const el = e.target.closest ? e.target.closest(".site-toggle[data-site]") : null;
      return el && root.contains(el) ? el : null;
    }}

    root.addEventListener("dragstart", function (e) {{
      const row = rowFromEvent(e);
      if (!row) return;
      if (e.target && e.target.closest && e.target.closest("input")) {{
        e.preventDefault();
        return;
      }}
      dragEl = row;
      row.classList.add("dragging");
      try {{
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", row.getAttribute("data-site") || "");
      }} catch (err) {{}}
    }});

    root.addEventListener("dragend", function () {{
      if (dragEl) dragEl.classList.remove("dragging");
      root.querySelectorAll(".site-toggle.drag-over").forEach(function (el) {{
        el.classList.remove("drag-over");
      }});
      dragEl = null;
      const order = applySiteOrder(currentSiteOrder());
      saveState("newsextract.siteOrder", order);
      setStatus("Site order updated (browser only)");
    }});

    root.addEventListener("dragover", function (e) {{
      if (!dragEl) return;
      e.preventDefault();
      const over = rowFromEvent(e);
      if (!over || over === dragEl) return;
      root.querySelectorAll(".site-toggle.drag-over").forEach(function (el) {{
        if (el !== over) el.classList.remove("drag-over");
      }});
      over.classList.add("drag-over");
      const rect = over.getBoundingClientRect();
      const before = (e.clientY - rect.top) < rect.height / 2;
      if (before) root.insertBefore(dragEl, over);
      else root.insertBefore(dragEl, over.nextSibling);
    }});

    root.addEventListener("drop", function (e) {{
      e.preventDefault();
    }});
  }})();

  document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      const state = currentSiteState();
      saveState(SITE_KEY, state);
      applySites(state);
      setStatus("Site view updated (browser only)");
    }});
  }});

  document.querySelectorAll("#lang-toggles input[data-lang]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      const state = currentLangState();
      saveState(LANG_KEY, state);
      applyLanguages(state);
      setStatus("Language filter updated (browser only)");
    }});
  }});

  document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{
    cb.addEventListener("change", function () {{
      const state = currentCatState();
      saveState(CAT_KEY, state);
      applyCats(state);
      setStatus("Category view updated (browser only)");
    }});
  }});

  document.getElementById("toggle-external").addEventListener("change", function () {{
    const show = currentExternalState();
    saveState(EXT_KEY, show);
    applyExternal(show);
    setStatus("Off-site link view updated (browser only)");
  }});

  document.getElementById("toggle-only-new").addEventListener("change", function () {{
    const only = currentOnlyNewState();
    saveState(ONLY_NEW_KEY, only);
    applyOnlyNew(only);
    setStatus(only ? "Showing only new articles" : "Showing new and previously seen");
  }});

  document.getElementById("toggle-all-new").addEventListener("change", function () {{
    const show = document.getElementById("toggle-all-new").checked;
    saveState(ALL_NEW_KEY, show);
    applyAllNew(show);
    setStatus(show ? "All new feed shown" : "All new feed hidden");
  }});

  document.getElementById("toggle-clusters").addEventListener("change", function () {{
    const show = document.getElementById("toggle-clusters").checked;
    saveState(CLUSTERS_KEY, show);
    applyClusters(show);
    setStatus(show ? "Cluster view shown" : "Cluster view hidden");
  }});

  document.getElementById("btn-cluster").addEventListener("click", function () {{
    const show = !document.getElementById("clusters") ||
      document.getElementById("clusters").classList.contains("hidden");
    saveState(CLUSTERS_KEY, show);
    applyClusters(show);
    if (show) {{
      const sec = document.getElementById("clusters");
      if (sec) sec.scrollIntoView({{ behavior: "smooth", block: "start" }});
    }}
    setStatus(show ? "Cluster view shown" : "Cluster view hidden");
  }});

  document.getElementById("btn-cluster-list").addEventListener("click", function () {{
    saveState("newsextract.clusterView", "list");
    applyClusterView("list");
    setStatus("Cluster list view");
  }});
  document.getElementById("btn-cluster-graph").addEventListener("click", function () {{
    saveState("newsextract.clusterView", "graph");
    applyClusterView("graph");
    setStatus("Cluster graph view");
  }});
  document.getElementById("btn-cluster-min-dec").addEventListener("click", function () {{
    const n = applyClusterMinSize(currentClusterMinSize() - 1);
    saveState("newsextract.clusterMinSize", n);
    setStatus("Minimum cluster size " + n);
  }});
  document.getElementById("btn-cluster-min-inc").addEventListener("click", function () {{
    const n = applyClusterMinSize(currentClusterMinSize() + 1);
    saveState("newsextract.clusterMinSize", n);
    setStatus("Minimum cluster size " + n);
  }});

  document.getElementById("toggle-dim-opened").addEventListener("change", function () {{
    const dim = document.getElementById("toggle-dim-opened").checked;
    saveState(DIM_OPENED_KEY, dim);
    applyDimOpened(dim);
    setStatus(dim ? "Opened links dimmed" : "Opened links not dimmed");
  }});

  document.getElementById("toggle-opened-today").addEventListener("change", function () {{
    const show = document.getElementById("toggle-opened-today").checked;
    saveState(OPENED_TODAY_KEY, show);
    applyOpenedTodayPanel(show);
    setStatus(show ? "Opened today shown" : "Opened today hidden");
  }});

  document.getElementById("toggle-dark").addEventListener("change", function () {{
    const dark = document.getElementById("toggle-dark").checked;
    saveState(DARK_MODE_KEY, dark);
    applyDarkMode(dark);
    setStatus(dark ? "Dark mode on" : "Light mode on");
  }});

  document.getElementById("btn-theme").addEventListener("click", function () {{
    const dark = !document.body.classList.contains("dark");
    saveState(DARK_MODE_KEY, dark);
    applyDarkMode(dark);
    setStatus(dark ? "Dark mode on" : "Light mode on");
  }});

  document.addEventListener("click", function (e) {{
    const a = e.target && e.target.closest ? e.target.closest("li.headline a") : null;
    if (!a) return;
    const li = a.closest("li.headline");
    const href = (li && li.getAttribute("data-href")) || a.href;
    markOpened(href);
  }});

  document.getElementById("btn-clear-storage").addEventListener("click", function () {{
    if (!confirm("Clear all NewsExtract browser settings, opened-link history, and cookies for this page?")) return;
    clearAllBrowserData();
    setStatus("Cleared — reloading…");
    location.reload();
  }});

  document.getElementById("slider-bg-strength").addEventListener("input", function () {{
    const n = applyBgStrength(currentBgStrength());
    saveState("newsextract.bgStrength", n);
    setStatus("Color strength " + n + "%");
  }});

  document.getElementById("input-seen-limit").addEventListener("input", function () {{
    const n = applySeenLimit(currentSeenLimit());
    saveState("newsextract.seenLimit", n);
    setStatus("Showing up to " + n + " previously seen per site");
  }});

  document.getElementById("input-highlight-words").addEventListener("input", function () {{
    const words = applyHighlightWords(currentHighlightWords());
    saveState("newsextract.highlightWords", words);
    const n = parseHighlightWords(words).length;
    setStatus(n ? ("Highlighting " + n + " keyword" + (n === 1 ? "" : "s")) : "Keyword highlight cleared");
  }});

  document.getElementById("input-exclude-words").addEventListener("input", function () {{
    const words = applyExcludeWords(currentExcludeWords());
    saveState("newsextract.excludeWords", words);
    const n = parseHighlightWords(words).length;
    setStatus(n ? ("Excluding " + n + " keyword" + (n === 1 ? "" : "s")) : "Exclude filter cleared");
  }});

  document.getElementById("btn-sites-all").addEventListener("click", function () {{
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{ cb.checked = true; }});
    const state = currentSiteState();
    saveState(SITE_KEY, state);
    applySites(state);
  }});

  document.getElementById("btn-sites-none").addEventListener("click", function () {{
    document.querySelectorAll("#site-toggles input[data-site]").forEach(function (cb) {{ cb.checked = false; }});
    const state = currentSiteState();
    saveState(SITE_KEY, state);
    applySites(state);
  }});

  document.getElementById("btn-cats-all").addEventListener("click", function () {{
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{ cb.checked = true; }});
    const state = currentCatState();
    saveState(CAT_KEY, state);
    applyCats(state);
  }});

  document.getElementById("btn-cats-none").addEventListener("click", function () {{
    document.querySelectorAll("#cat-toggles input[data-category]").forEach(function (cb) {{ cb.checked = false; }});
    const state = currentCatState();
    saveState(CAT_KEY, state);
    applyCats(state);
  }});

  document.getElementById("btn-export").addEventListener("click", function () {{
    const siteState = currentSiteState();
    const catState = currentCatState();
    const showExternal = currentExternalState();
    const onlyNew = currentOnlyNewState();
    const bgStrength = currentBgStrength();
    const seenLimit = currentSeenLimit();
    const highlightWords = currentHighlightWords();
    const excludeWords = currentExcludeWords();
    const siteOrder = currentSiteOrder();
    const showAllNew = document.getElementById("toggle-all-new").checked;
    const showClusters = document.getElementById("toggle-clusters").checked;
    const dimOpened = document.getElementById("toggle-dim-opened").checked;
    const showOpenedToday = document.getElementById("toggle-opened-today").checked;
    const darkMode = document.getElementById("toggle-dark").checked;
    const languages = currentLangState();

    const nextSites = JSON.parse(JSON.stringify(sitesConfig));
    Object.keys(siteState).forEach(function (id) {{
      if (!nextSites[id] || typeof nextSites[id] !== "object") nextSites[id] = {{}};
      nextSites[id].enabled = siteState[id] !== false;
    }});
    delete nextSites._settings;

    const nextSettings = JSON.parse(JSON.stringify(settingsConfig || {{}}));
    nextSettings.show_external = showExternal;
    nextSettings.only_new = onlyNew;
    nextSettings.bg_strength = bgStrength;
    nextSettings.seen_limit = seenLimit;
    nextSettings.highlight_words = highlightWords;
    nextSettings.exclude_words = excludeWords;
    nextSettings.site_order = siteOrder;
    nextSettings.show_all_new = showAllNew;
    nextSettings.show_clusters = showClusters;
    nextSettings.cluster_view = currentClusterView();
    nextSettings.cluster_min_size = currentClusterMinSize();
    nextSettings.dim_opened = dimOpened;
    nextSettings.show_opened_today = showOpenedToday;
    nextSettings.dark_mode = darkMode;
    nextSettings.languages = languages;

    const nextCats = JSON.parse(JSON.stringify(categoriesConfig));
    Object.keys(catState).forEach(function (id) {{
      if (!nextCats[id] || typeof nextCats[id] !== "object") nextCats[id] = {{ label: id, color: "#eee", match: [] }};
      nextCats[id].enabled = catState[id] !== false;
    }});

    downloadJson("sites.json", nextSites);
    setTimeout(function () {{
      downloadJson("settings.json", nextSettings);
    }}, 400);
    setTimeout(function () {{
      downloadJson("categories.json", nextCats);
      setStatus("Downloaded sites.json + settings.json + categories.json — replace project files for next run");
    }}, 800);
  }});
}})();
</script>
  <footer class="page-footer">NewsExtract - by Theo Engell, only front pages are read, cookies are used to store your preferences - released under GPL3</footer>
</body>
</html>
"""
