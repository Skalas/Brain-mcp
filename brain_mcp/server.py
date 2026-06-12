"""brain-mcp: MCP server exposing the second-brain vault."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import kind_ops, kinds as kinds_mod, vault, vectors, writes

mcp = FastMCP("brain")

KINDS = kinds_mod.load_kinds()


@mcp.tool()
def search_notes(
    query: str,
    type: str | None = None,
    updated_since: str | None = None,
    k: int = 10,
    include_archived: bool = False,
) -> list[dict]:
    """Search vault notes by substring across aliases, ids, and body.

    Args:
        query: text to look for (case-insensitive).
        type: optional frontmatter type filter (person, project, topic, ref, meeting, daily, conversation).
        updated_since: optional YYYY-MM-DD; only include notes whose `updated` >= this date.
        k: max results.
        include_archived: if true, also search notes in _archive/ (excluded by default).
            Each result carries an `archived` boolean. Note: semantic/hybrid search
            cannot surface archived notes (their vectors are pruned on archive) — use
            this substring search, or read_note(id) directly, to reach them.
    """
    return vault.search_notes(query, type, updated_since, k, include_archived)


@mcp.tool()
def read_note(id: str) -> dict:
    """Read a full note by id (filename stem). Returns frontmatter + body."""
    note = vault.find_note_by_id(id)
    if note is None:
        raise ValueError(f"Note {id!r} not found in notes/, daily/, meetings/, or conversations/.")
    return note.to_payload()


@mcp.tool()
def list_index(name: str) -> str:
    """Read a Map-of-Content (MOC). Allowed names: people, projects, topics, timeline, tags, README."""
    return vault.read_index(name)


@mcp.tool()
def get_doctrine() -> str:
    """Return the vault's write conventions and schema (`_system/CLAUDE.md`).

    Read this BEFORE any vault write (append_section, create_note, create_dated,
    add_<kind>, update_<kind>) to learn:
    - Frontmatter schema per note type (person, project, topic, ref, meeting, daily, conversation)
    - Filename rules (kebab-case, dated folders, collision handling)
    - Body structure (dated H2 sections, wikilinks, language matching)
    - Guardrails — what requires user confirmation (deletes, renames, bulk ops, git commits)

    Cheap to call (~5KB). Call once per session before the first write.
    """
    return vault.read_doctrine()


@mcp.tool()
def list_workflows() -> list[dict]:
    """List workflow recipes available in the vault.

    A "workflow" is a multi-step procedure declared in `_system/recipes/*.md`
    without `kind:`/`class:` frontmatter (those are kind definitions — see
    list_kinds). Examples: conversation-append-pass, function-first-project-rewrite.

    Returns: [{name, description}, ...]. Call get_workflow(name) to read the body.
    """
    return vault.list_workflows()


@mcp.tool()
def get_workflow(name: str) -> str:
    """Return a workflow recipe's full body (instructions to follow).

    Call after list_workflows() identifies a matching workflow. The body is
    plain markdown — read it and follow the steps exactly.
    """
    return vault.get_workflow(name)


@mcp.tool()
def append_section(id: str, body: str, date: str | None = None) -> dict:
    """Append a dated H2 section to an existing note.

    For structured entities (book, recipe, task, etc.), prefer the per-kind tools
    (add_<kind>, update_<kind>) — they enforce schema. Use this only for
    unstructured updates to existing person/project/topic/ref notes.

    Before first write, call get_doctrine() for body structure and language rules.

    Args:
        id: note id (filename stem). Note must already exist.
        body: markdown body for the new section (no need to include the `## YYYY-MM-DD` header — it's added).
        date: optional YYYY-MM-DD; defaults to today.
    """
    return writes.append_section(id, body, date)


@mcp.tool()
def edit_note(id: str, old_string: str, new_string: str) -> dict:
    """Edit a note's body by exact-string replacement. DESTRUCTIVE — removes content permanently.

    Confirm with the user before calling: state exactly what will be removed or
    replaced and get an explicit yes. Never call this speculatively.

    Use for corrections and removals of content that should not be in the note.
    For adding new content, use append_section instead. Frontmatter is not
    editable here — use update_<kind> for structured fields.

    Workflow: read_note(id) first, copy the exact text (whitespace included) as
    old_string. It must match the body exactly once; the call fails with a count
    if it matches zero or multiple times.

    Args:
        id: note id (filename stem).
        old_string: exact text currently in the body. Must be unique within the note.
        new_string: replacement text. Pass "" to delete old_string entirely.
    """
    return writes.edit_note(id, old_string, new_string)


@mcp.tool()
def archive_note(id: str, strip_refs: bool = False) -> dict:
    """Retire a whole note: move it to _archive/, prune its vectors, drop it from
    active search and MOCs. DESTRUCTIVE but REVERSIBLE — undo with restore_note.

    Use this to remove a subject altogether (e.g. a one-off contact you no longer
    track), as opposed to edit_note which only changes body text within a note.

    Confirm with the user before calling: name the note and get an explicit yes.

    The archived file still exists, so [[wikilinks]] to it from other notes keep
    resolving in Obsidian. By default this only REPORTS which notes reference it
    (returned as `referenced_by`). Pass strip_refs=true ONLY if the user explicitly
    wants those links unlinked — that edits other notes and is not undone by restore.

    Args:
        id: note id (filename stem) to archive.
        strip_refs: if true, rewrite every referencing note to unlink [[id]]
            (keeping any display alias as plain text). Default false.
    """
    return writes.archive_note(id, strip_refs)


@mcp.tool()
def restore_note(id: str, type: str | None = None) -> dict:
    """Restore an archived note: move it from _archive/ back to its active folder,
    flip status to active, drop the `archived` date, and reindex.

    Args:
        id: archived note id (filename stem).
        type: optional frontmatter type override if it changed while archived.
    """
    return writes.restore_note(id, type)


@mcp.tool()
def create_note(
    type: str,
    slug: str,
    frontmatter: dict,
    body: str,
) -> dict:
    """Create a new note in notes/. Fails if slug already exists.

    For structured entities (book, recipe, task), prefer add_<kind> — it enforces
    the recipe's field contract and runs side effects. Use this only for
    unstructured new entities (person, project, topic, ref) without a kind.

    Before first write, call get_doctrine() for the frontmatter schema and required fields per type.

    Args:
        type: one of person, project, topic, ref.
        slug: kebab-case filename stem.
        frontmatter: extra frontmatter fields (org, role, aliases, tags, etc.). id/created/updated are set automatically.
        body: markdown body.
    """
    return writes.create_note(type, slug, frontmatter, body)


@mcp.tool()
def create_dated(
    kind: str,
    slug: str | None,
    body: str,
    frontmatter: dict | None = None,
    date: str | None = None,
) -> dict:
    """Create a new file in daily/, meetings/, or conversations/.

    Before first write, call get_doctrine() for filename rules and required frontmatter per dated kind.

    Args:
        kind: daily, meetings, or conversations.
        slug: required for meetings/conversations; ignored for daily.
        body: markdown body.
        frontmatter: optional extra frontmatter (attendees, project, source, etc.).
        date: optional YYYY-MM-DD; defaults to today.
    """
    return writes.create_dated(kind, slug, body, frontmatter, date)


@mcp.tool()
def search_semantic(
    query: str,
    k: int = 10,
    type: str | None = None,
) -> list[dict]:
    """Semantic (vector) search over vault sections using local bge-m3 embeddings.

    Returns top-k notes ranked by cosine similarity. Best for "I don't remember the
    exact words" recall and cross-language queries (ES ↔ EN).

    Args:
        query: natural-language query.
        k: max results.
        type: optional frontmatter type filter.
    """
    return vectors.search_semantic(query, k=k, type_filter=type)


@mcp.tool()
def search_hybrid(
    query: str,
    k: int = 10,
    type: str | None = None,
) -> list[dict]:
    """Hybrid search: reciprocal-rank fusion of semantic + grep results.

    Use this as the default search when you want both exact-string and conceptual recall.
    """
    return vectors.search_hybrid(query, k=k, type_filter=type)


@mcp.tool()
def reindex_vectors(full: bool = False, note_id: str | None = None) -> dict:
    """Rebuild the vector index.

    Args:
        full: if true, walk the entire vault (prunes deleted notes too).
        note_id: if provided, reindex only that note.
    """
    if note_id:
        return vectors.reindex_note(note_id)
    if full:
        return vectors.reindex_all(prune=True)
    result = vectors.ensure_indexed()
    return result or {"status": "already_indexed"}


@mcp.tool()
def list_kinds() -> list[dict]:
    """List all structured-entity kinds registered in the vault.

    A "kind" is a typed entity (book, recipe, task, …) declared by a recipe
    file under `_system/recipes/`. Each kind exposes its own add/find/update
    tools depending on its interaction class.

    Call this first when you're not sure what's available.
    """
    return [
        {
            "name": k.name,
            "class": k.klass,
            "description": k.description,
            "fields_required": list(k.required_fields),
            "fields_optional": list(k.optional_fields),
            "retrieval_filters": list(k.retrieval_filters),
            "states": list(k.states) if k.klass == "living-list" else None,
        }
        for k in KINDS.values()
    ]


@mcp.tool()
def get_recipe(kind: str) -> dict:
    """Get the recipe (instructions) for a kind.

    Call this BEFORE invoking `add_<kind>` so you know what fields to gather,
    what enrichment steps the recipe expects, and what the resulting note
    should look like.
    """
    if kind not in KINDS:
        raise ValueError(f"Unknown kind {kind!r}. Available: {sorted(KINDS)}")
    k = KINDS[kind]
    return {
        "kind": k.name,
        "class": k.klass,
        "description": k.description,
        "fields_required": list(k.required_fields),
        "fields_optional": list(k.optional_fields),
        "states": list(k.states) if k.klass == "living-list" else None,
        "default_state": k.default_state,
        "instructions": k.body,
    }


def _add_description(kind: kinds_mod.Kind) -> str:
    parts = [f"Add a new {kind.name}."]
    if kind.description:
        parts.append(kind.description)
    parts.append(f"Required fields in `data`: {list(kind.required_fields)}.")
    if kind.optional_fields:
        parts.append(f"Optional: {list(kind.optional_fields)}.")
    parts.append(f"`body` is the freeform markdown content of the resulting note.")
    parts.append(f"For full instructions (enrichment, side effects, body shape), call get_recipe('{kind.name}').")
    return " ".join(parts)


def _find_description(kind: kinds_mod.Kind) -> str:
    filters = list(kind.retrieval_filters) + ["tag"]
    return (
        f"Find {kind.name} entries. "
        f"`where` keys must be one of {filters}; scalar values match exactly, list values match any-of. "
        f"`tag` matches if the value appears in the note's tags."
    )


def _register_archive_kind(kind: kinds_mod.Kind) -> None:
    """Register `add_<kind>` and `find_<kind>` for an archive-class kind."""

    @mcp.tool(name=f"add_{kind.name}", description=_add_description(kind))
    def add_archive(data: dict, body: str = "") -> dict:
        return kind_ops.add(kind, data, body)

    @mcp.tool(name=f"find_{kind.name}", description=_find_description(kind))
    def find_archive(where: dict | None = None) -> list[dict]:
        return kind_ops.find(kind, where)


def _register_living_list_kind(kind: kinds_mod.Kind) -> None:
    """Register add/update/complete/list for a living-list-class kind."""

    @mcp.tool(name=f"add_{kind.name}", description=_add_description(kind))
    def add_ll(data: dict, body: str = "") -> dict:
        return kind_ops.add(kind, data, body)

    @mcp.tool(
        name=f"update_{kind.name}",
        description=(
            f"Patch fields on an existing {kind.name}. `id` is the note id (filename stem). "
            f"`patch` may include any of {list(kind.required_fields) + list(kind.optional_fields)} "
            f"or `state` (one of {list(kind.states)})."
        ),
    )
    def update_ll(id: str, patch: dict) -> dict:
        return kind_ops.update(kind, id, patch)

    @mcp.tool(
        name=f"complete_{kind.name}",
        description=(
            f"Mark a {kind.name} as complete. Flips `state` to {kind.terminal_states[0]!r} "
            f"(the first terminal state). `id` is the note id."
            if kind.terminal_states
            else f"Mark a {kind.name} as complete."
        ),
    )
    def complete_ll(id: str) -> dict:
        return kind_ops.complete(kind, id)

    _list_desc = (
        f"List {kind.name} entries. By default excludes terminal states "
        f"({list(kind.terminal_states)}); pass `where={{state: '<terminal>'}}` to see completed items. "
        f"Other filters: {list(kind.retrieval_filters) + ['state', 'tag']}. "
        "If this returns an empty list [], do NOT retry the same query; assume no matching items exist."
    )
    if kind.list_hint:
        _list_desc = f"{kind.list_hint}\n\n{_list_desc}"

    @mcp.tool(name=f"list_{kind.name}", description=_list_desc)
    def list_ll(where: dict | None = None) -> list[dict]:
        return kind_ops.list_items(kind, where)


for _kind in KINDS.values():
    if _kind.klass == "archive":
        _register_archive_kind(_kind)
    elif _kind.klass == "living-list":
        _register_living_list_kind(_kind)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
