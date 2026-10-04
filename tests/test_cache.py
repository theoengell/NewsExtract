"""Page-cache detection times and life-span."""

from datetime import datetime, timedelta, timezone

from nex.cache import (
    resolve_site_cache_ttl,
    snapshot_page_cache,
    split_new_vs_seen,
)
from nex.presentation import build_combined_html

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
URL = "https://example.com/"
HREF = "https://example.com/a"


def _entry(cache):
    return cache["example.com"]["hrefs"][HREF]


def test_first_run_records_detection_time():
    cache = {}
    new, seen, first = split_new_vs_seen(
        [("Hello there story", HREF)],
        URL,
        cache,
        ttl_days=90,
        now=NOW,
    )
    assert first is True
    assert new == []
    assert seen == [("Hello there story", HREF)]
    entry = _entry(cache)
    assert entry["first_seen"].startswith("2026-10-04T12:00:00")
    assert entry["last_seen"] == entry["first_seen"]
    assert entry["hits"] == 1
    assert "estimated" not in entry


def test_later_run_keeps_first_seen_and_counts_hits():
    cache = {}
    split_new_vs_seen(
        [("Hello there story", HREF)],
        URL,
        cache,
        ttl_days=90,
        now=NOW,
    )
    later = NOW + timedelta(days=2)
    new, seen, first = split_new_vs_seen(
        [("Hello there story updated", HREF)],
        URL,
        cache,
        ttl_days=90,
        now=later,
    )
    assert first is False
    assert new == []
    assert seen == [("Hello there story updated", HREF)]
    entry = _entry(cache)
    assert entry["first_seen"].startswith("2026-10-04T12:00:00")
    assert entry["last_seen"].startswith("2026-10-06T12:00:00")
    assert entry["title"] == "Hello there story updated"
    assert entry["hits"] == 2


def test_legacy_title_string_stays_untracked_until_seen_again():
    cache = {
        "example.com": {
            "hrefs": {HREF: "Old title"},
            "last_run": "2026-09-01T00:00:00+00:00",
        }
    }
    new, seen, first = split_new_vs_seen(
        [("Fresh title", HREF)],
        URL,
        cache,
        ttl_days=90,
        now=NOW,
    )
    assert first is False
    assert new == []
    assert seen == [("Fresh title", HREF)]
    entry = _entry(cache)
    assert "first_seen" not in entry
    assert entry["last_seen"].startswith("2026-10-04T12:00:00")
    assert entry["hits"] == 1
    assert "estimated" not in entry


def test_legacy_page_not_on_the_front_page_is_anchored_not_dropped():
    other = "https://example.com/old"
    cache = {
        "example.com": {
            "hrefs": {other: "Still remembered"},
            "last_run": "2026-09-20T08:00:00+00:00",
        }
    }
    split_new_vs_seen(
        [("Something else entirely", HREF)],
        URL,
        cache,
        ttl_days=30,
        now=NOW,
    )
    entry = cache["example.com"]["hrefs"][other]
    assert entry["title"] == "Still remembered"
    assert entry["last_seen"] == "2026-09-20T08:00:00+00:00"
    assert entry["estimated"] is True
    assert "first_seen" not in entry


def test_expired_page_is_detected_as_new_again():
    cache = {
        "example.com": {
            "hrefs": {
                HREF: {
                    "title": "Old",
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-01-01T00:00:00+00:00",
                    "hits": 4,
                }
            },
            "last_run": "2026-01-01T00:00:00+00:00",
        }
    }
    new, seen, first = split_new_vs_seen(
        [("Old", HREF)],
        URL,
        cache,
        ttl_days=30,
        now=NOW,
    )
    assert first is False
    assert seen == []
    assert new == [("Old", HREF)]
    entry = _entry(cache)
    assert entry["hits"] == 1
    assert entry["first_seen"].startswith("2026-10-04T12:00:00")


def test_expired_page_absent_from_the_front_page_is_forgotten():
    cache = {
        "example.com": {
            "hrefs": {
                "https://example.com/gone": {
                    "title": "Gone",
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-01-01T00:00:00+00:00",
                    "hits": 2,
                },
                HREF: {
                    "title": "Kept",
                    "first_seen": "2026-09-01T00:00:00+00:00",
                    "last_seen": "2026-09-20T00:00:00+00:00",
                    "hits": 3,
                },
            }
        }
    }
    split_new_vs_seen([], URL, cache, ttl_days=30, now=NOW)
    hrefs = cache["example.com"]["hrefs"]
    assert "https://example.com/gone" not in hrefs
    assert hrefs[HREF]["title"] == "Kept"
    assert hrefs[HREF]["hits"] == 3


def test_exact_life_span_boundary_is_kept():
    last = NOW - timedelta(days=90)
    cache = {
        "example.com": {
            "hrefs": {
                HREF: {
                    "title": "Boundary",
                    "first_seen": last.isoformat(),
                    "last_seen": last.isoformat(),
                    "hits": 1,
                }
            }
        }
    }
    split_new_vs_seen([], URL, cache, ttl_days=90, now=NOW)
    assert HREF in cache["example.com"]["hrefs"]


def test_site_life_span_overrides_the_default():
    assert resolve_site_cache_ttl({"cache_ttl_days": 60}, None) == 60
    assert resolve_site_cache_ttl(
        {"cache_ttl_days": 60},
        {"cache_ttl_days": 180},
    ) == 180
    assert resolve_site_cache_ttl({"cache_ttl_days": 60}, {}) == 60
    assert resolve_site_cache_ttl({}, {"cache_ttl_days": 5000}) == 730
    assert resolve_site_cache_ttl({"cache_ttl_days": "nope"}, None) == 90


def test_snapshot_groups_pages_and_marks_the_front_page():
    cache = {
        "www.example.com": {
            "last_run": "2026-10-04T12:00:00+00:00",
            "hrefs": {
                HREF: {
                    "title": "On the page",
                    "first_seen": "2026-10-01T00:00:00+00:00",
                    "last_seen": "2026-10-04T12:00:00+00:00",
                    "hits": 2,
                },
                "https://example.com/old": "Legacy title",
            },
            "last_display": {
                "new": [],
                "seen": [["On the page", HREF]],
                "is_first_run": False,
            },
        }
    }
    snap = snapshot_page_cache(
        cache,
        [{"site_id": "example", "name": "Example", "url": "https://example.com/"}],
        {"example": {"cache_ttl_days": 180}},
    )
    assert len(snap["sites"]) == 1
    site = snap["sites"][0]
    assert site["id"] == "example"
    assert site["domain"] == "www.example.com"
    assert site["ttl"] == 180
    by_href = {row[1]: row for row in site["pages"]}
    assert by_href[HREF][0] == "On the page"
    assert by_href[HREF][5] == 1
    assert by_href[HREF][4] == 2
    legacy = by_href["https://example.com/old"]
    assert legacy[0] == "Legacy title"
    assert legacy[2] == ""
    assert legacy[5] == 0


def test_developer_html_escapes_cache_payload():
    cache = {
        "example.com": {
            "hrefs": {
                "https://example.com/a?q=1&x=2": {
                    "title": "Hello </script><script>alert(1)</script>",
                    "first_seen": "2026-10-04T12:00:00+00:00",
                    "last_seen": "2026-10-04T12:00:00+00:00",
                    "hits": 1,
                }
            },
            "last_display": {
                "new": [["Hello </script><script>alert(1)</script>", "https://example.com/a?q=1&x=2"]],
                "seen": [],
                "is_first_run": False,
            },
        }
    }
    page = build_combined_html(
        [{
            "site_id": "example",
            "name": "Example",
            "url": URL,
            "new_results": [],
            "seen_results": [],
            "is_first_run": False,
        }],
        {"other": {"label": "Other", "color": "#eee", "match": [], "enabled": True}},
        {"example": {"enabled": True, "name": "Example", "url": URL, "cache_ttl_days": 120}},
        {"developer_mode": True, "cache_ttl_days": 90, "languages": {"da": True}},
        {"example": ("example.com",)},
        page_cache=cache,
    )
    assert 'id="toggle-developer" checked' in page
    assert 'id="btn-dev"' in page
    assert 'id="btn-dev" hidden' not in page
    assert 'id="developer" class="dev-screen" hidden' in page
    assert "Show pages" in page
    assert 'id="dev-fold-graph" open' in page
    assert 'id="dev-cache-graph"' in page
    assert 'id="dev-fold-life">' in page
    assert 'id="dev-fold-pages">' in page
    assert 'id="developer"' in page
    assert 'data-dev="1"' in page
    assert 'value="120"' in page
    assert "<script>alert" not in page
    assert "\\u003c/script\\u003e" in page
    assert "Detected pages" in page
