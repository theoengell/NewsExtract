"""Parity: grammar extract matches Python extract on HTML fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from parsers import get_parser_by_id, refresh
from parsers.base import normalize_for_dedup
from parsers.engine import compare_candidates, get_grammar_by_id

FIXTURES = Path(__file__).resolve().parent / "fixtures"
HTML_DIR = FIXTURES / "html"
EXPECTED_DIR = FIXTURES / "expected"

# Sites that have both a Python module and a grammar + HTML fixture.
PARITY_SITES = sorted(p.stem for p in HTML_DIR.glob("*.html")) if HTML_DIR.is_dir() else []


def _candidate_rows(cands):
    rows = []
    for text, score, href, *_rest in cands:
        rows.append(
            {
                "text": text,
                "score": score,
                "href": href,
                "key": normalize_for_dedup(text),
            }
        )
    rows.sort(key=lambda r: (r["key"], r["href"] or "", r["score"]))
    return rows


@pytest.fixture(scope="module", autouse=True)
def _refresh_parsers():
    refresh()


@pytest.mark.parametrize("site_id", PARITY_SITES)
def test_grammar_matches_python(site_id):
    grammar = get_grammar_by_id(site_id)
    assert grammar is not None, f"missing grammar for {site_id}"
    mod = get_parser_by_id(site_id)

    html = (HTML_DIR / f"{site_id}.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    base = mod.DEFAULT_URL

    py_cands = mod.extract(soup, base)
    gr_cands = grammar.extract(soup, base)
    diff = compare_candidates(py_cands, gr_cands)
    assert diff["ok"], (
        f"{site_id} parity failed: "
        f"only_py={len(diff['only_py'])} "
        f"only_grammar={len(diff['only_grammar'])} "
        f"score_mismatch={len(diff['score_mismatch'])}\n"
        f"py={_candidate_rows(py_cands)}\n"
        f"gr={_candidate_rows(gr_cands)}"
    )


@pytest.mark.parametrize("site_id", PARITY_SITES)
def test_expected_golden_if_present(site_id):
    expected_path = EXPECTED_DIR / f"{site_id}.json"
    if not expected_path.is_file():
        pytest.skip("no golden expected file")
    grammar = get_grammar_by_id(site_id)
    mod = get_parser_by_id(site_id)
    html = (HTML_DIR / f"{site_id}.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    gr_cands = grammar.extract(soup, mod.DEFAULT_URL)
    actual = [
        {"text": t, "score": s, "href": h}
        for t, s, h, *_ in sorted(gr_cands, key=lambda c: (normalize_for_dedup(c[0]), c[2] or ""))
    ]
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    assert actual == expected
