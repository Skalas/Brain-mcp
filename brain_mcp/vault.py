"""Vault filesystem helpers: path resolution, frontmatter parsing, search."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

import yaml


VAULT_PATH = Path(os.environ.get("VAULT_PATH", "")).expanduser().resolve()
if not VAULT_PATH or not VAULT_PATH.exists():
    raise RuntimeError(
        f"VAULT_PATH env var must point to an existing directory. Got: {VAULT_PATH!r}"
    )

NOTES_DIR = VAULT_PATH / "notes"
DAILY_DIR = VAULT_PATH / "daily"
MEETINGS_DIR = VAULT_PATH / "meetings"
CONVERSATIONS_DIR = VAULT_PATH / "conversations"
INDEX_DIR = VAULT_PATH / "_index"
SYSTEM_DIR = VAULT_PATH / "_system"
ARCHIVE_DIR = VAULT_PATH / "_archive"

ACTIVE_DIRS = (NOTES_DIR, DAILY_DIR, MEETINGS_DIR, CONVERSATIONS_DIR)
WRITABLE_DIRS = (NOTES_DIR, DAILY_DIR, MEETINGS_DIR, CONVERSATIONS_DIR, ARCHIVE_DIR)
READONLY_DIRS = (SYSTEM_DIR, INDEX_DIR, VAULT_PATH / ".obsidian")

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


class VaultError(Exception):
    """Raised when an operation violates vault guardrails."""


@dataclass
class Note:
    id: str
    path: Path
    frontmatter: dict
    body: str

    def to_payload(self, include_body: bool = True) -> dict:
        out = {
            "id": self.id,
            "path": str(self.path.relative_to(VAULT_PATH)),
            "frontmatter": self.frontmatter,
        }
        if include_body:
            out["body"] = self.body
        return out


def assert_inside_vault(path: Path) -> Path:
    resolved = path.resolve()
    if VAULT_PATH not in resolved.parents and resolved != VAULT_PATH:
        raise VaultError(f"Path {resolved} is outside the vault.")
    for ro in READONLY_DIRS:
        if ro in resolved.parents or resolved == ro:
            raise VaultError(f"Path {resolved} is in a read-only area ({ro.name}).")
    return resolved


def parse_note(path: Path) -> Note:
    text = path.read_text(encoding="utf-8")
    match = FRONTMATTER_RE.match(text)
    if not match:
        return Note(id=path.stem, path=path, frontmatter={}, body=text)
    fm_raw, body = match.group(1), match.group(2)
    try:
        fm = yaml.safe_load(fm_raw) or {}
    except yaml.YAMLError:
        fm = {"_parse_error": True}
    return Note(id=path.stem, path=path, frontmatter=fm, body=body)


def find_note_by_id(note_id: str) -> Note | None:
    for d in (NOTES_DIR, DAILY_DIR, MEETINGS_DIR, CONVERSATIONS_DIR, ARCHIVE_DIR):
        candidate = d / f"{note_id}.md"
        if candidate.exists():
            return parse_note(candidate)
    return None


def iter_notes(
    folders: Iterable[Path] | None = None, *, include_archived: bool = False
) -> Iterable[Note]:
    if folders is None:
        folders = ACTIVE_DIRS + (ARCHIVE_DIR,) if include_archived else ACTIVE_DIRS
    for folder in folders:
        if not folder.exists():
            continue
        for path in folder.glob("*.md"):
            yield parse_note(path)


def search_notes(
    query: str,
    type_filter: str | None = None,
    updated_since: str | None = None,
    k: int = 10,
    include_archived: bool = False,
) -> list[dict]:
    query_lower = query.lower()
    hits: list[tuple[int, Note, str]] = []

    for note in iter_notes(include_archived=include_archived):
        if type_filter and note.frontmatter.get("type") != type_filter:
            continue
        if updated_since:
            updated = str(note.frontmatter.get("updated", ""))
            if updated < updated_since:
                continue

        score = 0
        snippet = ""

        # Highest signal: alias or title match.
        aliases = note.frontmatter.get("aliases") or []
        if any(query_lower in str(a).lower() for a in aliases):
            score += 100
        if query_lower in note.id.lower():
            score += 50

        # Body match — first occurrence becomes the snippet.
        body_lower = note.body.lower()
        idx = body_lower.find(query_lower)
        if idx != -1:
            score += 10
            start = max(0, idx - 60)
            end = min(len(note.body), idx + len(query) + 60)
            snippet = note.body[start:end].replace("\n", " ").strip()

        if score > 0:
            # Tie-break recent updates higher.
            updated = str(note.frontmatter.get("updated", ""))
            hits.append((score, note, snippet or _first_line(note.body), updated))

    hits.sort(key=lambda t: (t[0], t[3]), reverse=True)
    out = []
    for score, note, snippet, _ in hits[:k]:
        out.append(
            {
                "id": note.id,
                "path": str(note.path.relative_to(VAULT_PATH)),
                "type": note.frontmatter.get("type"),
                "updated": note.frontmatter.get("updated"),
                "score": score,
                "snippet": snippet,
                "archived": note.path.parent == ARCHIVE_DIR,
            }
        )
    return out


def wikilink_re(note_id: str) -> re.Pattern:
    """Match `[[note_id]]`, `[[note_id|alias]]`, `[[note_id#heading]]` (any combo)."""
    return re.compile(r"\[\[" + re.escape(note_id) + r"((?:#|\|)[^\]]*)?\]\]")


def find_references(note_id: str) -> list[Note]:
    """Active notes whose body wikilinks to `note_id` (the archived note excluded)."""
    pattern = wikilink_re(note_id)
    return [
        note
        for note in iter_notes()
        if note.id != note_id and pattern.search(note.body)
    ]


def _first_line(body: str) -> str:
    for line in body.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line[:160]
    return ""


def read_index(name: str) -> str:
    allowed = {"people", "projects", "topics", "timeline", "tags", "README"}
    if name not in allowed:
        raise VaultError(f"Unknown index {name!r}. Allowed: {sorted(allowed)}")
    path = INDEX_DIR / f"{name}.md"
    if not path.exists():
        raise VaultError(f"Index {name} does not exist at {path}")
    return path.read_text(encoding="utf-8")


def read_doctrine() -> str:
    """Return the vault's conventions file (`_system/CLAUDE.md`) verbatim."""
    path = SYSTEM_DIR / "CLAUDE.md"
    if not path.exists():
        raise VaultError(f"Vault doctrine missing at {path}")
    return path.read_text(encoding="utf-8")


def list_workflows() -> list[dict]:
    """List workflow recipes (recipes without `kind:` frontmatter — those are kinds).

    Returns: [{name, description}, ...] where description is the first non-heading
    line of the recipe body (truncated to 200 chars).
    """
    recipes_dir = SYSTEM_DIR / "recipes"
    if not recipes_dir.exists():
        return []
    out: list[dict] = []
    import yaml as _yaml
    for path in sorted(recipes_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        body = text
        if match:
            try:
                fm = _yaml.safe_load(match.group(1)) or {}
                if isinstance(fm, dict) and "kind" in fm and "class" in fm:
                    continue  # this is a kind definition, not a workflow
            except _yaml.YAMLError:
                pass
            body = match.group(2)
        description = ""
        for line in body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                description = line[:200]
                break
        if not description:
            for line in body.splitlines():
                if line.startswith("# "):
                    description = line[2:].strip()
                    break
        out.append({"name": path.stem, "description": description})
    return out


def get_workflow(name: str) -> str:
    """Return a workflow recipe's full body (frontmatter + markdown)."""
    path = SYSTEM_DIR / "recipes" / f"{name}.md"
    if not path.exists():
        raise VaultError(f"Workflow {name!r} not found at {path}")
    return path.read_text(encoding="utf-8")


def today_iso() -> str:
    return date.today().isoformat()
