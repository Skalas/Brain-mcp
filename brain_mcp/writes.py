"""Write operations: append, create, reindex."""
from __future__ import annotations

import logging
import re
import subprocess

import yaml

from .vault import (
    ARCHIVE_DIR,
    CONVERSATIONS_DIR,
    DAILY_DIR,
    DEFAULT_CONTEXT,
    MEETINGS_DIR,
    NOTES_DIR,
    SAFE_STEM_RE,
    SYSTEM_DIR,
    VAULT_PATH,
    VaultError,
    assert_inside_vault,
    find_note_by_id,
    find_references,
    parse_note,
    sub_outside_code,
    today_iso,
    wikilink_re,
)

logger = logging.getLogger(__name__)

REINDEX_SCRIPT = SYSTEM_DIR / "scripts" / "reindex.sh"
SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
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


def _reindex_vectors_only(note_id: str | None) -> str:
    """In-process vector reindex fallback when the external script is absent."""
    if not note_id:
        return "[reindex: skipped — no reindex script; MOCs not regenerated]"
    try:
        from . import vectors

        vectors.reindex_note(note_id)
        return "[reindex: vectors only — no reindex script, MOCs not regenerated]"
    except Exception as exc:  # never block a write on the vector path
        return f"[reindex: skipped — {exc}]"


def run_reindex(note_id: str | None = None) -> str:
    if not REINDEX_SCRIPT.exists():
        logger.warning(
            "Reindex script missing (%s); falling back to in-process vector reindex. "
            "MOCs/_index will not be regenerated.",
            REINDEX_SCRIPT.name,
        )
        return _reindex_vectors_only(note_id)
    result = subprocess.run(
        ["bash", str(REINDEX_SCRIPT)],
        capture_output=True,
        text=True,
        cwd=VAULT_PATH,
        timeout=120,
    )
    if result.returncode != 0:
        raise VaultError(f"reindex failed: {result.stderr}")
    stdout = result.stdout.strip()

    if note_id:
        try:
            from . import vectors

            vectors.reindex_note(note_id)
        except Exception as exc:  # never block a write on the vector path
            return f"{stdout}\n[vectors: skipped — {exc}]"
    return stdout


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
    note.path.write_text(render_note(fm, new_body), encoding="utf-8")

    reindex_out = run_reindex(note.id)
    return {
        "id": note.id,
        "path": str(note.path.relative_to(VAULT_PATH)),
        "updated": section_date,
        "reindex": reindex_out,
    }


def edit_note(note_id: str, old_string: str, new_string: str) -> dict:
    if not old_string:
        raise VaultError("old_string must be non-empty.")
    if old_string == new_string:
        raise VaultError("old_string and new_string are identical; nothing to edit.")

    note = find_note_by_id(note_id)
    if note is None:
        raise VaultError(f"Note {note_id!r} does not exist.")
    assert_inside_vault(note.path)

    count = note.body.count(old_string)
    if count == 0:
        raise VaultError(
            f"old_string not found in the body of {note_id!r}. "
            "Read the note first and copy the exact text, including whitespace. "
            "Note: frontmatter is not editable with this tool."
        )
    if count > 1:
        raise VaultError(
            f"old_string appears {count} times in {note_id!r}. "
            "Include surrounding context so it matches exactly once."
        )

    new_body = note.body.replace(old_string, new_string)
    fm = dict(note.frontmatter)
    fm["updated"] = today_iso()
    note.path.write_text(render_note(fm, new_body), encoding="utf-8")

    reindex_out = run_reindex(note.id)
    return {
        "id": note.id,
        "path": str(note.path.relative_to(VAULT_PATH)),
        "removed": _excerpt(old_string),
        "inserted": _excerpt(new_string),
        "updated": fm["updated"],
        "reindex": reindex_out,
    }


def _excerpt(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… [{len(text)} chars]"


def _unlink_reference(match: re.Match, note_id: str) -> str:
    """Turn a `[[note_id|alias]]`/`[[note_id#h]]`/`[[note_id]]` match into plain text."""
    suffix = match.group(1) or ""
    if suffix.startswith("|"):
        return suffix[1:]  # keep the display alias
    return note_id  # `[[id]]` or `[[id#heading]]` → the id as plain text


def archive_note(note_id: str, strip_refs: bool = False) -> dict:
    """Soft-archive a note: move it to `_archive/`, prune its vectors, drop it from
    active search/MOCs. Reversible via restore_note. DESTRUCTIVE — confirm first."""
    note = find_note_by_id(note_id)
    if note is None:
        raise VaultError(f"Note {note_id!r} does not exist.")
    assert_inside_vault(note.path)
    if note.path.parent == ARCHIVE_DIR:
        raise VaultError(f"Note {note_id!r} is already archived.")

    dest = ARCHIVE_DIR / note.path.name
    if dest.exists():
        raise VaultError(
            f"{dest.relative_to(VAULT_PATH)} already exists; resolve the collision first."
        )

    # Ensure the archive dir exists BEFORE mutating other notes, so a mkdir
    # failure can't leave refs stripped while the note never moves.
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    referencing = find_references(note_id)
    stripped: list[str] = []
    if strip_refs and referencing:
        pattern = wikilink_re(note_id)
        for ref in referencing:
            new_body = sub_outside_code(
                pattern, lambda m: _unlink_reference(m, note_id), ref.body
            )
            fm = dict(ref.frontmatter)
            fm["updated"] = today_iso()
            ref.path.write_text(render_note(fm, new_body), encoding="utf-8")
            run_reindex(ref.id)
            stripped.append(ref.id)

    fm = dict(note.frontmatter)
    fm["status"] = "archived"
    fm["archived"] = today_iso()
    dest.write_text(render_note(fm, note.body), encoding="utf-8")
    note.path.unlink()

    from . import vectors

    vec = vectors._delete_note(note_id, reason="archived")
    reindex_out = run_reindex()

    result = {
        "id": note_id,
        "archived_to": str(dest.relative_to(VAULT_PATH)),
        "vectors_pruned": vec["deleted"],
        "referenced_by": [r.id for r in referencing],
        "reindex": reindex_out,
    }
    if strip_refs:
        result["stripped_refs_from"] = stripped
    elif referencing:
        result["note"] = (
            f"{len(referencing)} note(s) still wikilink to {note_id!r}; links resolve "
            "from _archive/. Pass strip_refs=true to unlink them."
        )
    return result


def restore_note(note_id: str, note_type: str | None = None) -> dict:
    """Move an archived note back to active (notes/), flip status to active, reindex."""
    if not SAFE_STEM_RE.match(note_id):
        raise VaultError(f"Invalid note id {note_id!r}.")
    src = ARCHIVE_DIR / f"{note_id}.md"
    assert_inside_vault(src)  # belt-and-suspenders: never read/delete outside _archive/
    if not src.exists():
        raise VaultError(f"No archived note {note_id!r} in _archive/.")
    note = parse_note(src)

    # Dated kinds belong in their own folders; everything else returns to notes/.
    folder_by_type = {
        "daily": DAILY_DIR,
        "meeting": MEETINGS_DIR,
        "conversation": CONVERSATIONS_DIR,
    }
    folder = folder_by_type.get(note.frontmatter.get("type", ""), NOTES_DIR)
    dest = folder / src.name
    if dest.exists():
        raise VaultError(
            f"{dest.relative_to(VAULT_PATH)} already exists; resolve the collision first."
        )

    fm = dict(note.frontmatter)
    fm["status"] = "active"
    fm.pop("archived", None)
    if note_type:
        fm["type"] = note_type
    dest.write_text(render_note(fm, note.body), encoding="utf-8")
    src.unlink()

    reindex_out = run_reindex(note_id)
    return {
        "id": note_id,
        "restored_to": str(dest.relative_to(VAULT_PATH)),
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
        "context": DEFAULT_CONTEXT,
        "links": [],
        **frontmatter,
    }
    fm["id"] = slug  # cannot be overridden
    fm["created"] = today
    fm["updated"] = today

    path.write_text(render_note(fm, body), encoding="utf-8")
    reindex_out = run_reindex(slug)
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
        "context": DEFAULT_CONTEXT,
        "links": [],
        **(frontmatter or {}),
    }
    fm["id"] = path.stem
    fm["created"] = file_date

    path.write_text(render_note(fm, body), encoding="utf-8")
    reindex_out = run_reindex(path.stem)
    return {
        "id": path.stem,
        "path": str(path.relative_to(VAULT_PATH)),
        "reindex": reindex_out,
    }


def render_note(frontmatter: dict, body: str) -> str:
    fm_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{fm_text}\n---\n{body.rstrip()}\n"
