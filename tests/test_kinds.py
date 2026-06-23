"""Slug rendering and validation (#25) plus data validation."""
import pytest

from brain_mcp.kinds import KindError, load_kinds, render_slug, slugify, validate_data
from brain_mcp.writes import SLUG_RE, validate_slug
from brain_mcp.vault import VaultError


def test_slugify_strips_accents():
    assert slugify("José Pérez") == "jose-perez"
    assert slugify("Hello, World!") == "hello-world"


def test_slugify_non_latin_fallback_nonempty():
    assert slugify("日本語タイトル")  # must not be empty
    assert slugify("Книга")


@pytest.mark.parametrize("good", ["abc", "a-b-c", "a1", "2026-01-01-note"])
def test_slug_re_accepts(good):
    assert SLUG_RE.match(good)


@pytest.mark.parametrize("bad", ["abc-", "-abc", "a--", ""])
def test_slug_re_rejects(bad):
    assert not SLUG_RE.match(bad)
    if bad:
        with pytest.raises(VaultError):
            validate_slug(bad)


def test_render_slug_falls_back_to_kind_name():
    kind = load_kinds()["task"]
    # title slugifies to empty -> kind name
    assert render_slug(kind, {"title": "!!!"}) == "task"


def test_validate_data_missing_and_unknown():
    kind = load_kinds()["task"]
    with pytest.raises(KindError):
        validate_data(kind, {})  # missing title
    with pytest.raises(KindError):
        validate_data(kind, {"title": "x", "bogus": 1})  # unknown field
    validate_data(kind, {"title": "x", "project": "p"})  # ok
