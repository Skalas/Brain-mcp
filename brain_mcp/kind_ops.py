"""Kind-aware operations: add, find, list, update, complete, side effects.

Sits between the Kind registry (kinds.py) and the low-level vault writers
(writes.py). All per-kind business logic lives here.
"""
from __future__ import annotations

import re
from pathlib import Path

from . import writes
from .kinds import Kind, render_slug, validate_data
from .vault import (
    NOTES_DIR,
    VAULT_PATH,
    Note,
    VaultError,
    assert_inside_vault,
    find_note_by_id,
    iter_notes,
    today_iso,
)

DEFAULT_CONTEXT = "personal"
SIDE_EFFECT_LINE_TEMPLATE = "- [[{id}]] — {title}"


def add(kind: Kind, data: dict, body: str = "") -> dict:
    """Create a new note for `kind` from `data` + `body`.

    Validates data against the kind's schema, renders the target slug, builds
    frontmatter (including `kind:` marker and any living-list initial state),
    writes via writes.create_note, then runs declared side effects.
    """
    validate_data(kind, data)
    slug = render_slug(kind, data)

    title = data.get("title", "")
    fm_extra: dict = {
        "kind": kind.name,
        "aliases": [title] if title else [],
        "tags": [kind.name],
        "context": DEFAULT_CONTEXT,
    }
    for field in kind.all_fields:
        if field in data:
            fm_extra[field] = data[field]
    if kind.klass == "living-list" and kind.default_state:
        fm_extra["state"] = kind.default_state

    result = writes.create_note(kind.target_type, slug, fm_extra, body)

    side_effects_report = _apply_side_effects(kind, slug, fm_extra)
    if side_effects_report:
        result["side_effects"] = side_effects_report
    return result


def find(kind: Kind, where: dict | None = None) -> list[dict]:
    """Find notes matching `kind` with optional retrieval filters.

    `where` keys must be declared in the kind's `retrieval.filters` (plus the
    universal `tag` filter and, for living-list kinds, `state`). Scalar values
    match exactly; list values match if note value is in the list. For `tag`,
    the value must appear in `tags`.
    """
    where = where or {}
    _validate_filter(kind, where, allow_state=True)

    out: list[dict] = []
    for note in iter_notes((NOTES_DIR,)):
        if note.frontmatter.get("kind") != kind.name:
            continue
        if not _matches_filter(note, where):
            continue
        out.append(_note_summary(note, kind))
    out.sort(key=lambda r: r.get("updated") or "", reverse=True)
    return out


def list_items(kind: Kind, where: dict | None = None) -> list[dict]:
    """List living-list items. Terminal states are excluded unless the caller
    explicitly filters for a terminal state.
    """
    if kind.klass != "living-list":
        raise VaultError(f"list_{kind.name} is only available for living-list kinds.")
    where = where or {}
    results = find(kind, where)
    terminal = set(kind.terminal_states)
    asked_for_terminal = where.get("state") in terminal
    if terminal and not asked_for_terminal:
        results = [r for r in results if r.get("state") not in terminal]
    return results


def update(kind: Kind, id: str, patch: dict) -> dict:
    """Patch declared fields (plus `state` for living-list) on an existing note."""
    note = find_note_by_id(id)
    if note is None:
        raise VaultError(f"Note {id!r} not found.")
    if note.frontmatter.get("kind") != kind.name:
        raise VaultError(
            f"Note {id!r} is kind {note.frontmatter.get('kind')!r}, not {kind.name!r}."
        )

    allowed = set(kind.all_fields)
    if kind.klass == "living-list":
        allowed.add("state")
    bad = [k for k in patch if k not in allowed]
    if bad:
        raise VaultError(
            f"Cannot patch fields {bad} on kind {kind.name!r}. Allowed: {sorted(allowed)}"
        )
    if "state" in patch and patch["state"] not in kind.states:
        raise VaultError(
            f"state {patch['state']!r} not in {list(kind.states)}"
        )

    fm = dict(note.frontmatter)
    fm.update(patch)
    fm["updated"] = today_iso()
    note.path.write_text(writes.render_note(fm, note.body), encoding="utf-8")
    reindex_out = writes.run_reindex(note.id)
    return {
        "id": note.id,
        "path": str(note.path.relative_to(VAULT_PATH)),
        "updated": fm["updated"],
        "patched": list(patch.keys()),
        "reindex": reindex_out,
    }


def complete(kind: Kind, id: str) -> dict:
    """Flip a living-list note's `state` to the first declared terminal state."""
    if kind.klass != "living-list":
        raise VaultError(f"complete is only available for living-list kinds.")
    if not kind.terminal_states:
        raise VaultError(
            f"kind {kind.name!r} has no terminal_states declared; nothing to complete to."
        )
    target = kind.terminal_states[0]
    return update(kind, id, {"state": target})


def _apply_side_effects(kind: Kind, slug: str, fm: dict) -> list[dict]:
    """Run declared side effects after a successful add. Best-effort: failures don't roll back the write."""
    report: list[dict] = []
    for effect in kind.side_effects:
        if not isinstance(effect, dict):
            continue
        if "append_to" in effect:
            target_rel = effect["append_to"]
            line_template = effect.get("line") or SIDE_EFFECT_LINE_TEMPLATE
            try:
                line = _render_template(line_template, {"id": slug, **fm})
                _append_line(VAULT_PATH / target_rel, line, kind)
                report.append({"append_to": target_rel, "status": "ok"})
            except Exception as exc:  # never block the write on a side-effect failure
                report.append({"append_to": target_rel, "status": "failed", "error": str(exc)})
    return report


def _append_line(target: Path, line: str, kind: Kind) -> None:
    """Append a markdown line to a vault file. Auto-creates a stub if missing.

    Uses POSIX append-mode for the line write so concurrent writers (e.g., openclaw
    + this machine on the same iCloud-synced vault) don't lose lines via TOCTOU.
    If the file doesn't exist, stub + first line are written in a single call —
    no two-step window where the stub exists without the entry.
    """
    assert_inside_vault(target)  # rejects paths outside the vault and read-only areas
    line_text = line.rstrip() + "\n"
    if not target.exists():
        stub_fm = {
            "id": target.stem,
            "type": "topic",
            "aliases": [],
            "tags": [kind.name, "index"],
            "created": today_iso(),
            "updated": today_iso(),
            "status": "active",
            "context": DEFAULT_CONTEXT,
            "links": [],
        }
        stub_body = f"# {target.stem.title()}\n\nAuto-generated index of {kind.name} entries.\n\n{line_text}"
        target.write_text(writes.render_note(stub_fm, stub_body), encoding="utf-8")
        return
    with target.open("a", encoding="utf-8") as fh:
        fh.write(line_text)


def _render_template(template: str, data: dict) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1)
        value = data.get(key, "")
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)
    return re.sub(r"\{([a-z][a-z0-9_]*)\}", repl, template)


def _matches_filter(note: Note, where: dict) -> bool:
    fm = note.frontmatter
    for key, value in where.items():
        if key == "tag":
            tags = fm.get("tags") or []
            if value not in tags:
                return False
            continue
        actual = fm.get(key)
        if isinstance(value, list):
            if actual not in value:
                return False
        else:
            if actual != value:
                return False
    return True


def _validate_filter(kind: Kind, where: dict, allow_state: bool) -> None:
    allowed = set(kind.retrieval_filters) | {"tag"}
    if allow_state and kind.klass == "living-list":
        allowed.add("state")
    bad = [k for k in where if k not in allowed]
    if bad:
        raise VaultError(
            f"Filters {bad} not allowed for kind {kind.name!r}. Allowed: {sorted(allowed)}"
        )


def _note_summary(note: Note, kind: Kind) -> dict:
    fm = note.frontmatter
    summary: dict = {
        "id": note.id,
        "title": fm.get("title") or (fm.get("aliases") or [None])[0] or note.id,
        "updated": fm.get("updated"),
    }
    if kind.klass == "living-list":
        summary["state"] = fm.get("state")
    for field in kind.all_fields:
        if field in fm and field != "title":
            summary[field] = fm[field]
    return summary
