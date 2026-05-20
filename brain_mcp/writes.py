"""Write operations: append, create, reindex."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

from .vault import (
    CONVERSATIONS_DIR,
    DAILY_DIR,
    MEETINGS_DIR,
    NOTES_DIR,
    SYSTEM_DIR,
    VAULT_PATH,
    VaultError,
    assert_inside_vault,
    find_note_by_id,
    parse_note,
    today_iso,
)

REINDEX_SCRIPT = SYSTEM_DIR / "scripts" / "reindex.sh"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

VALID_TYPES = {"person", "project", "topic", "ref"}
VALID_DATED_KINDS = {"daily", "meetings", "conversations"}


def validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        raise VaultError(
            f"Slug {slug!r} must be kebab-case (lowercase letters, digits, hyphens)."
        )


def validate_date(s: str) -> None:
    if not DATE_RE.match(s):
        raise VaultError(f"Date {s!r} must be YYYY-MM-DD.")


def run_reindex() -> str:
    if not REINDEX_SCRIPT.exists():
        raise VaultError(f"reindex script missing at {REINDEX_SCRIPT}")
    result = subprocess.run(
        ["bash", str(REINDEX_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=VAULT_PATH,
        timeout=120,
    )
    if result.returncode != 0:
        raise VaultError(f"reindex failed: {result.stderr}")
    return result.stdout.strip()


def append_section(note_id: str, body: str, section_date: str | None = None) -> dict:
    note = find_note_by_id(note_id)
    if note is None:
        raise VaultError(
            f"Note {note_id!r} does not exist. Use create_note first."
        )
    assert_inside_vault(note.path)

    section_date = section_date or today_iso()
    validate_date(section_date)

    fm = dict(note.frontmatter)
    fm["updated"] = section_date

    new_body = note.body.rstrip() + f"\n\n## {section_date}\n\n{body.strip()}\n"
    note.path.write_text(_render_note(fm, new_body), encoding="utf-8")

    reindex_out = run_reindex()
    return {
        "id": note.id,
        "path": str(note.path.relative_to(VAULT_PATH)),
        "updated": section_date,
        "reindex": reindex_out,
    }


def create_note(
    note_type: str, slug: str, frontmatter: dict, body: str
) -> dict:
    if note_type not in VALID_TYPES:
        raise VaultError(
            f"type must be one of {sorted(VALID_TYPES)}, got {note_type!r}."
        )
    validate_slug(slug)
    path = NOTES_DIR / f"{slug}.md"
    if path.exists():
        raise VaultError(
            f"Note {slug!r} already exists. Use append_section instead."
        )
    assert_inside_vault(path)

    today = today_iso()
    fm = {
        "id": slug,
        "type": note_type,
        "aliases": [],
        "tags": [],
        "created": today,
        "updated": today,
        "status": "active",
        "context": "work",
        "links": [],
        **frontmatter,
    }
    fm["id"] = slug  # cannot be overridden
    fm["created"] = today
    fm["updated"] = today

    path.write_text(_render_note(fm, body), encoding="utf-8")
    reindex_out = run_reindex()
    return {
        "id": slug,
        "path": str(path.relative_to(VAULT_PATH)),
        "reindex": reindex_out,
    }


def create_dated(
    kind: str,
    slug: str | None,
    body: str,
    frontmatter: dict | None = None,
    file_date: str | None = None,
) -> dict:
    if kind not in VALID_DATED_KINDS:
        raise VaultError(
            f"kind must be one of {sorted(VALID_DATED_KINDS)}, got {kind!r}."
        )
    file_date = file_date or today_iso()
    validate_date(file_date)

    dir_map = {
        "daily": DAILY_DIR,
        "meetings": MEETINGS_DIR,
        "conversations": CONVERSATIONS_DIR,
    }
    folder = dir_map[kind]

    if kind == "daily":
        filename = f"{file_date}.md"
    else:
        if not slug:
            raise VaultError(f"slug is required for kind={kind!r}.")
        validate_slug(slug)
        filename = f"{file_date}-{slug}.md"

    path = folder / filename
    if path.exists():
        raise VaultError(f"{path.relative_to(VAULT_PATH)} already exists.")
    assert_inside_vault(path)

    type_map = {"daily": "daily", "meetings": "meeting", "conversations": "conversation"}
    fm = {
        "id": path.stem,
        "type": type_map[kind],
        "created": file_date,
        "updated": file_date,
        "status": "active",
        "context": "work",
        "links": [],
        **(frontmatter or {}),
    }
    fm["id"] = path.stem
    fm["created"] = file_date

    path.write_text(_render_note(fm, body), encoding="utf-8")
    reindex_out = run_reindex()
    return {
        "id": path.stem,
        "path": str(path.relative_to(VAULT_PATH)),
        "reindex": reindex_out,
    }


def _render_note(frontmatter: dict, body: str) -> str:
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm_text}\n---\n{body.rstrip()}\n"
