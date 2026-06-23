"""Vault filesystem helpers: path resolution, frontmatter parsing, search."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
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


# Fenced code blocks (``` … ``` or ~~~ … ~~~) and inline code spans (`…`).
# Wikilinks inside these are documentation examples, not real links, so we mask
# them before scanning. Keep this in sync with the vault reindex script's
# link-extraction so frontmatter `links:` and reference lookups agree.
FENCED_CODE_RE = re.compile(r"^[ \t]*(`{3,}|~{3,}).*?^[ \t]*\1[ \t]*$", re.DOTALL | re.MULTILINE)
INLINE_CODE_RE = re.compile(r"(`+)(?:.+?)\1")


def strip_code(text: str) -> str:
    """Blank out fenced code blocks and inline code spans.

    Wikilink syntax shown as an example (e.g. `[[wikilink]]` inside backticks)
    must not be mistaken for a real link.
    """
    text = FENCED_CODE_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    return text


def _code_spans(text: str) -> list[tuple[int, int]]:
    """Character ranges covered by fenced code blocks and inline code spans."""
    spans = [m.span() for m in FENCED_CODE_RE.finditer(text)]
    spans += [m.span() for m in INLINE_CODE_RE.finditer(text)]
    return sorted(spans)


def sub_outside_code(pattern: re.Pattern, repl, text: str) -> str:
    """Apply `pattern.sub(repl, ...)` to `text`, leaving code spans untouched."""
    code = _code_spans(text)

    def _repl(match: re.Match) -> str:
        start = match.start()
        if any(s <= start < e for s, e in code):
            return match.group(0)
        return repl(match)

    return pattern.sub(_repl, text)


def wikilink_re(note_id: str) -> re.Pattern:
    """Match `[[note_id]]`, `[[note_id|alias]]`, `[[note_id#heading]]` (any combo)."""
    return re.compile(r"\[\[" + re.escape(note_id) + r"((?:#|\|)[^\]]*)?\]\]")


def find_references(note_id: str) -> list[Note]:
    """Active notes whose body wikilinks to `note_id` (the archived note excluded).

    Wikilinks appearing only inside code spans/blocks are examples, not real
    references, so they are ignored.
    """
    pattern = wikilink_re(note_id)
    return [
        note
        for note in iter_notes()
        if note.id != note_id and pattern.search(strip_code(note.body))
    ]


# Any wikilink, capturing the target id (group 1) while discarding an optional
# `#heading` and/or `|alias` suffix. Mirrors `wikilink_re` but matches any target.
ANY_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")


def existing_note_ids(include_archived: bool = True) -> set[str]:
    """Set of note ids present in the vault (used to flag dangling outlinks)."""
    return {note.id for note in iter_notes(include_archived=include_archived)}


def extract_outlinks(body: str) -> list[str]:
    """Target ids wikilinked from `body`, in first-seen order, deduplicated.

    Links inside code spans/blocks are documentation examples, not real links,
    so they are masked out before scanning (same rule as `find_references`).
    """
    seen: dict[str, None] = {}
    for match in ANY_WIKILINK_RE.finditer(strip_code(body)):
        target = match.group(1).strip()
        if target:
            seen.setdefault(target, None)
    return list(seen)


def links_of(note_id: str) -> dict:
    """Outbound and inbound wikilinks for a note.

    Returns ``{"outbound": [...], "inbound": [...]}`` where each outbound entry is
    ``{"id", "dangling"}`` (dangling=True when the target note does not exist) and
    each inbound entry is ``{"id", "path"}``. Code-span links are ignored in both
    directions.
    """
    note = find_note_by_id(note_id)
    if note is None:
        raise VaultError(f"Note {note_id!r} not found.")
    known = existing_note_ids()
    outbound = [
        {"id": target, "dangling": target not in known}
        for target in extract_outlinks(note.body)
        if target != note_id
    ]
    inbound = [
        {"id": ref.id, "path": str(ref.path.relative_to(VAULT_PATH))}
        for ref in find_references(note_id)
    ]
    return {"outbound": outbound, "inbound": inbound}


MAX_NEIGHBORHOOD_DEPTH = 3
MAX_NEIGHBORHOOD_NODES = 100


def _vault_signature() -> tuple:
    """Cheap fingerprint of active notes (path, mtime, size) — only stats files.

    Used as a cache key so the wikilink graph is recomputed when any note changes
    and reused otherwise, instead of re-parsing the whole vault on every query.
    """
    sig = []
    for folder in ACTIVE_DIRS:
        if not folder.exists():
            continue
        for path in folder.glob("*.md"):
            st = path.stat()
            sig.append((str(path), st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig))


def _graph() -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, dict]]:
    """Directed wikilink graph over active notes.

    Returns ``(out_edges, in_edges, meta)``. Only edges whose target is an existing
    active note are kept (dangling links dropped). ``meta[id]`` carries ``type`` and
    ``title`` for node payloads. Cached on a vault mtime signature, so it recomputes
    only when notes change — never stale, but not re-parsed on every query.

    Callers must treat the returned structures as read-only (they are shared).
    """
    return _graph_cached(_vault_signature())


@lru_cache(maxsize=2)
def _graph_cached(_signature: tuple) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[str, dict]]:
    notes = list(iter_notes())
    ids = {n.id for n in notes}
    out: dict[str, set[str]] = {n.id: set() for n in notes}
    inn: dict[str, set[str]] = {n.id: set() for n in notes}
    meta: dict[str, dict] = {}
    for note in notes:
        meta[note.id] = {
            "type": note.frontmatter.get("type"),
            "title": note.frontmatter.get("title") or note.id,
        }
        for target in extract_outlinks(note.body):
            if target in ids and target != note.id:
                out[note.id].add(target)
                inn[target].add(note.id)
    return out, inn, meta


def centrality() -> dict[str, float]:
    """Degree centrality per active note, normalized to [0, 1] by the max degree.

    A note's degree is its count of distinct wikilink neighbors (inbound ∪
    outbound). Hub notes (heavily linked) score near 1.0; leaves near 0. Backed by
    the cached graph, so repeated calls between writes are free.
    """
    out, inn, meta = _graph()
    degree = {note_id: len(out[note_id] | inn[note_id]) for note_id in meta}
    max_degree = max(degree.values(), default=0)
    if max_degree == 0:
        return {note_id: 0.0 for note_id in degree}
    return {note_id: deg / max_degree for note_id, deg in degree.items()}


def neighborhood(note_id: str, depth: int = 1) -> dict:
    """Subgraph of notes within `depth` wikilink hops of `note_id`.

    Edges are followed in both directions (a link counts whether it points to or
    from a node). `depth` is clamped to [1, 3] and the node count to 100. Returns
    ``{root, depth, nodes: [{id, distance, type, title}], edges: [{source, target}],
    truncated}`` — edges preserve link direction within the returned node set.
    """
    out, inn, meta = _graph()
    if note_id not in meta:
        raise VaultError(f"Note {note_id!r} not found among active notes.")
    depth = max(1, min(depth, MAX_NEIGHBORHOOD_DEPTH))

    dist = {note_id: 0}
    order = [note_id]
    frontier = [note_id]
    truncated = False
    while frontier and not truncated:
        nxt: list[str] = []
        for cur in frontier:
            if dist[cur] >= depth:
                continue
            for nb in out[cur] | inn[cur]:
                if nb not in dist:
                    dist[nb] = dist[cur] + 1
                    order.append(nb)
                    nxt.append(nb)
                    if len(order) >= MAX_NEIGHBORHOOD_NODES:
                        truncated = True
                        break
            if truncated:
                break
        frontier = nxt

    node_set = set(order)
    nodes = [{"id": i, "distance": dist[i], **meta[i]} for i in order]
    edges = [
        {"source": s, "target": t}
        for s in node_set
        for t in out[s]
        if t in node_set
    ]
    return {
        "root": note_id,
        "depth": depth,
        "nodes": nodes,
        "edges": edges,
        "truncated": truncated,
    }


def path_between(a: str, b: str) -> dict:
    """Shortest wikilink path between two notes (links treated as undirected).

    Returns ``{connected, length, path: [id, ...], nodes: [{id, type, title}]}``.
    `length` is the hop count (0 when a == b); `connected` is False with an empty
    path when no chain of links joins them.
    """
    out, inn, meta = _graph()
    for note_id in (a, b):
        if note_id not in meta:
            raise VaultError(f"Note {note_id!r} not found among active notes.")
    if a == b:
        return {"connected": True, "length": 0, "path": [a], "nodes": [{"id": a, **meta[a]}]}

    from collections import deque

    prev: dict[str, str | None] = {a: None}
    queue = deque([a])
    while queue:
        cur = queue.popleft()
        if cur == b:
            break
        for nb in out[cur] | inn[cur]:
            if nb not in prev:
                prev[nb] = cur
                queue.append(nb)

    if b not in prev:
        return {"connected": False, "length": None, "path": [], "nodes": []}

    path: list[str] = []
    cur: str | None = b
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    path.reverse()
    return {
        "connected": True,
        "length": len(path) - 1,
        "path": path,
        "nodes": [{"id": i, **meta[i]} for i in path],
    }


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
