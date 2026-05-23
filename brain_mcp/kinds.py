"""Kind registry: parses `_system/recipes/*.md` into typed Kind objects.

A "kind" is a structured entity type (book, recipe, task) declared by a recipe
file under VAULT/_system/recipes/. The MCP loads these at startup and exposes
per-kind tools based on the kind's interaction class.

This module is pure domain logic — no MCP wiring, no I/O beyond reading the
recipe files. Server.py consumes the registry to register dynamic tools.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

from .vault import SYSTEM_DIR

RECIPES_DIR = SYSTEM_DIR / "recipes"
FM_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
VALID_CLASSES = frozenset({"archive", "living-list"})
KIND_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
SLUG_TEMPLATE_RE = re.compile(r"\{([a-z][a-z0-9_-]*)\}")


class KindError(Exception):
    """Raised when a recipe file is malformed or violates kind invariants."""


@dataclass(frozen=True)
class Kind:
    name: str
    klass: str
    description: str
    target_type: str
    slug_pattern: str
    required_fields: tuple[str, ...]
    optional_fields: tuple[str, ...]
    retrieval_filters: tuple[str, ...]
    side_effects: tuple[dict, ...]
    states: tuple[str, ...]
    default_state: str | None
    terminal_states: tuple[str, ...]
    body: str
    recipe_path: Path

    @property
    def all_fields(self) -> tuple[str, ...]:
        return self.required_fields + self.optional_fields


def _parse_recipe(path: Path) -> Kind | None:
    """Parse one recipe file. Returns None if it's a workflow recipe (no `kind:`)."""
    text = path.read_text(encoding="utf-8")
    match = FM_RE.match(text)
    if not match:
        return None
    try:
        fm = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        raise KindError(f"{path.name}: invalid YAML frontmatter — {exc}") from exc
    body = match.group(2).strip()

    if "kind" not in fm or "class" not in fm:
        return None

    name = fm["kind"]
    if not isinstance(name, str) or not KIND_NAME_RE.match(name):
        raise KindError(
            f"{path.name}: `kind` must be a lowercase identifier (letters/digits/underscore), got {name!r}"
        )

    klass = fm["class"]
    if klass not in VALID_CLASSES:
        raise KindError(
            f"{path.name}: `class` must be one of {sorted(VALID_CLASSES)}, got {klass!r}"
        )

    target = fm.get("target") or {}
    if not isinstance(target, dict):
        raise KindError(f"{path.name}: `target` must be a mapping")
    target_type = target.get("type", "topic")
    slug_pattern = target.get("slug_pattern") or f"{name}-{{title-kebab}}"

    fields_fm = fm.get("fields") or {}
    required = tuple(fields_fm.get("required") or ())
    optional = tuple(fields_fm.get("optional") or ())
    if "title" not in required and "title" not in optional:
        # title is special — drives slug rendering. Force it required.
        required = ("title",) + required

    retrieval = fm.get("retrieval") or {}
    filters = tuple(retrieval.get("filters") or ())

    side_effects = tuple(fm.get("side_effects") or ())

    states_fm = fm.get("states") or ()
    states = tuple(states_fm)
    default_state = fm.get("default_state")
    terminal_states = tuple(fm.get("terminal_states") or ())

    if klass == "living-list":
        if not states:
            raise KindError(f"{path.name}: living-list kind must declare `states`")
        if default_state is None:
            raise KindError(f"{path.name}: living-list kind must declare `default_state`")
        if default_state not in states:
            raise KindError(
                f"{path.name}: default_state {default_state!r} not in states {list(states)}"
            )
        for s in terminal_states:
            if s not in states:
                raise KindError(
                    f"{path.name}: terminal_state {s!r} not in states {list(states)}"
                )

    return Kind(
        name=name,
        klass=klass,
        description=fm.get("description", ""),
        target_type=target_type,
        slug_pattern=slug_pattern,
        required_fields=required,
        optional_fields=optional,
        retrieval_filters=filters,
        side_effects=side_effects,
        states=states,
        default_state=default_state,
        terminal_states=terminal_states,
        body=body,
        recipe_path=path,
    )


def load_kinds(recipes_dir: Path = RECIPES_DIR) -> dict[str, Kind]:
    """Load all kind definitions from the recipes directory.

    Files without `kind:` and `class:` in frontmatter are treated as workflow
    recipes (not kind definitions) and skipped silently.
    """
    out: dict[str, Kind] = {}
    if not recipes_dir.exists():
        return out
    for path in sorted(recipes_dir.glob("*.md")):
        kind = _parse_recipe(path)
        if kind is None:
            continue
        if kind.name in out:
            raise KindError(
                f"Duplicate kind {kind.name!r} declared by {kind.recipe_path.name} "
                f"and {out[kind.name].recipe_path.name}"
            )
        out[kind.name] = kind
    return out


def slugify(value: str) -> str:
    """Convert a string to a kebab-case slug. Strips accents, lowercases, hyphenates."""
    normalized = unicodedata.normalize("NFKD", str(value))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered)
    return cleaned.strip("-")


def render_slug(kind: Kind, data: dict) -> str:
    """Render a kind's slug_pattern with data values.

    Placeholders are `{field-kebab}` (slugifies the value) or `{field}` (verbatim).
    """
    def _replace(match: re.Match) -> str:
        token = match.group(1)
        if token.endswith("-kebab"):
            field = token[: -len("-kebab")]
            return slugify(data.get(field, ""))
        return str(data.get(token, ""))

    rendered = SLUG_TEMPLATE_RE.sub(_replace, kind.slug_pattern)
    return rendered.strip("-") or kind.name


def validate_data(kind: Kind, data: dict) -> None:
    """Check that `data` satisfies the kind's field contract.

    Raises KindError on missing required fields or unknown fields.
    """
    if not isinstance(data, dict):
        raise KindError(f"data must be a dict, got {type(data).__name__}")
    missing = [f for f in kind.required_fields if f not in data or data[f] in (None, "")]
    if missing:
        raise KindError(
            f"kind {kind.name!r}: missing required fields {missing}. "
            f"Required: {list(kind.required_fields)}; optional: {list(kind.optional_fields)}"
        )
    allowed = set(kind.all_fields)
    unknown = [k for k in data if k not in allowed]
    if unknown:
        raise KindError(
            f"kind {kind.name!r}: unknown fields {unknown}. "
            f"Allowed: {sorted(allowed)}"
        )
