"""Validate all grammar YAML files against the JSON schema."""

from __future__ import annotations

from parsers.engine import (
    GRAMMAR_DIR,
    iter_country_dirs,
    iter_grammar_paths,
    load_country_meta,
    validate_all,
)


def test_all_grammars_validate():
    errors = validate_all()
    assert errors == [], "\n".join(errors)


def test_grammar_dir_has_yaml():
    files = [p for p, _meta in iter_grammar_paths(GRAMMAR_DIR)]
    assert files, f"Expected YAML grammars under {GRAMMAR_DIR}/<country>/"


def test_country_meta_files():
    countries = list(iter_country_dirs(GRAMMAR_DIR))
    assert countries, f"Expected country folders with country.json under {GRAMMAR_DIR}"
    for country_dir in countries:
        meta = load_country_meta(country_dir)
        assert meta["country"] == country_dir.name.lower()
        assert meta["language"] in {"da", "en", "sv", "no"}
        assert meta["name"]
