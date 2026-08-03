"""Parity: grammar extract matches golden expected fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from parsers import get_parser_by_id, refresh
from parsers.base import normalize_for_dedup
from parsers.engine import get_grammar_by_id

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HTML_DIR = FIXTURES / "html"
EXPECTED_DIR = FIXTURES / "expected"

PARITY_SITES = sorted(p.stem for p in HTML_DIR.glob("*.html")) if HTML_DIR.is_dir() else []

# sn.dk front page is JS-hydrated; fixture extract mocks the CDP priority API.
_SN_MOCK_PAYLOAD = {
    "status": "success",
    "data": [
        {
            "id": 1,
            "headline": "Fixture headline about local Sjælland news today",
            "path": "art1/roskilde-kommune/nyheder/fixture-headline-about-local-sjaelland-news-today",
            "slug": "fixture-headline-about-local-sjaelland-news-today",
        },
        {
            "id": 2,
            "headline": "Second fixture story from the priority API response",
            "path": "art2/holbaek-kommune/112/second-fixture-story",
            "slug": "second-fixture-story",
        },
    ],
}


@pytest.fixture(scope="module", autouse=True)
def _refresh_parsers():
    refresh()


def _extract(site_id, soup, base_url):
    grammar = get_grammar_by_id(site_id)
    if site_id != "sn":
        return grammar.extract(soup, base_url)

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = _SN_MOCK_PAYLOAD
    with patch("requests.get", return_value=mock_resp):
        return grammar.extract(soup, base_url)


@pytest.mark.parametrize("site_id", PARITY_SITES)
def test_grammar_matches_expected(site_id):
    expected_path = EXPECTED_DIR / f"{site_id}.json"
    assert expected_path.is_file(), f"missing golden file for {site_id}"

    grammar = get_grammar_by_id(site_id)
    assert grammar is not None, f"missing grammar for {site_id}"
    mod = get_parser_by_id(site_id)

    html = (HTML_DIR / f"{site_id}.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    gr_cands = _extract(site_id, soup, mod.DEFAULT_URL)
    actual = [
        {"text": t, "score": s, "href": h}
        for t, s, h, *_ in sorted(
            gr_cands, key=lambda c: (normalize_for_dedup(c[0]), c[2] or "")
        )
    ]
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected


@pytest.mark.parametrize("site_id", PARITY_SITES)
def test_registry_exposes_grammar_site(site_id):
    mod = get_parser_by_id(site_id)
    assert mod.SITE_ID == site_id
    assert callable(mod.extract)
