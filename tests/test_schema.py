"""Validate all grammar YAML files against the JSON schema."""

from __future__ import annotations

from parsers.engine import GRAMMAR_DIR, validate_all


def test_all_grammars_validate():
    errors = validate_all()
    assert errors == [], "\n".join(errors)


def test_grammar_dir_has_yaml():
    files = list(GRAMMAR_DIR.glob("*.yaml")) if GRAMMAR_DIR.is_dir() else []
    assert files, f"Expected YAML grammars in {GRAMMAR_DIR}"
