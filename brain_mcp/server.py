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
    """Read a full note by id (filename stem).

    Returns frontmatter + body plus a `links` field carrying the note's graph
    edges: `{outbound: [{id, dangling}], inbound: [{id, path}]}`. Use these to
    follow a thread (read a linked note) instead of re-searching from scratch.
    """
    note = vault.find_note_by_id(id)
    if note is None:
        raise ValueError(f"Note {id!r} not found in notes/, daily/, meetings/, or conversations/.")
    payload = note.to_payload()
    payload["links"] = vault.links_of(id)
    return payload


@mcp.tool()
def links_of(id: str) -> dict:
    """Return a note's wikilink graph edges without its body.

    Args:
        id: note id (filename stem).

    Returns:
        {"outbound": [{id, dangling}], "inbound": [{id, path}]} — outbound are
        notes this one links to (dangling=true if the target doesn't exist);
        inbound are notes that link here (backlinks). Links inside code
        spans/blocks are ignored in both directions.
    """
    return vault.links_of(id)


@mcp.tool()
def neighborhood(id: str, depth: int = 1) -> dict:
    """Return the subgraph of notes within `depth` wikilink hops of a note.

    Answers "what's around X" — the cluster of notes connected to this one. Links
    are followed in both directions. Use this to explore a topic's surroundings;
    use path_between to find how two specific notes connect.

    Args:
        id: note id (filename stem) to center on.
        depth: hop radius, clamped to 1..3 (default 1).

    Returns:
        {root, depth, nodes: [{id, distance, type, title}], edges: [{source,
        target}], truncated} — `distance` is hops from root; `edges` preserve link
        direction within the returned nodes; `truncated` is true if the 100-node
        cap was hit.
    """
    return vault.neighborhood(id, depth)


@mcp.tool()
def path_between(a: str, b: str) -> dict:
    """Find the shortest wikilink path connecting two notes.

    Answers "how do these two relate" — e.g. how a person connects to a project.
    Links are treated as undirected for pathfinding.

    Args:
        a: source note id (filename stem).
        b: target note id (filename stem).

    Returns:
        {connected, length, path: [id, ...], nodes: [{id, type, title}]} — `length`
        is the hop count (0 if a == b); `connected` is false with an empty path when
        no chain of links joins them.
    """
    return vault.path_between(a, b)


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
def get_consolidation_policy() -> str:
    """Return the cross-agent consolidation coexistence policy (`_system/dream-policy.md`).

    The single source of shared rules every independent consolidation agent
    (Claude Code / dream, OpenClaw / Nico, Hermes) MUST obey when writing to this
    vault or shared databases: section signatures, guardrail domains,
    durable-vs-ephemeral (incl. coding-session mode), finance.db dedup, REM
    propose-only, and ingest boundaries.

    Call once per consolidation run, BEFORE writing. The returned text carries a
    `policy_version` in its frontmatter — stamp it into the run journal so drift
    is detectable. Each agent runs its OWN pipeline; this is the policy, not the
    procedure.
    """
    return vault.read_consolidation_policy()


@mcp.tool()
def get_architecture() -> str:
    """Return the knowledge-ecosystem architecture map (`_system/architecture.md`).

    A reference map (not enforced): the SSOT/serving/writer topology, the
    differentiated-ingestion registry (which agent ingests which sources, via
    which stager, to where), what is centralized vs. local, and known gaps.
    Read this to reason about coverage and boundaries.
    """
    return vault.read_architecture()


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
    structural_weight: float = 0.1,
) -> list[dict]:
    """Hybrid search: semantic + grep fusion, re-ranked by graph proximity.

    Use this as the default search when you want both exact-string and conceptual recall.

    Text hits (semantic + grep) are the entry points; a personalized PageRank over
    the wikilink graph, seeded on those hits, then re-ranks them and can surface a
    connective note — one matching neither the words nor the embedding query but
    linked between several strong hits. Pass structural_weight=0 to rank on pure
    text relevance (no graph step), or raise it to lean harder on connectivity.

    Args:
        query: text / natural-language query.
        k: max results.
        type: optional frontmatter type filter.
        structural_weight: graph-proximity blend weight (default 0.1; 0 disables).
    """
    return vectors.search_hybrid(query, k=k, type_filter=type, structural_weight=structural_weight)


@mcp.tool()
def search_graph(
    query: str,
    k: int = 10,
    type: str | None = None,
    neighbors_per_seed: int = 5,
    edge_factor: float = 0.5,
) -> list[dict]:
    """Hybrid search expanded with the graph: returns matches AND their 1-hop context.

    Runs hybrid search for seed notes, then pulls in the notes one wikilink away
    (outbound links + backlinks) so related context surfaces alongside direct hits.
    Use this when you want to follow a thread — "this topic and what connects to it"
    — rather than only the closest text matches. For pure relevance, use
    search_hybrid.

    Each result has `source`: "seed" (a direct hit) or "graph" (pulled in via a
    link); graph results carry `neighbor_of` listing the seed ids that surfaced them.
    Neighbor scores are the seed score decayed by `edge_factor`, so seeds rank first
    and context follows. Bounded to one hop and `neighbors_per_seed` per seed.

    Args:
        query: natural-language query.
        k: max seed hits (and a cap on graph neighbors).
        type: optional frontmatter type filter, applied to seeds and neighbors.
        neighbors_per_seed: max 1-hop neighbors pulled per seed (default 5).
        edge_factor: neighbor score = seed score * this (default 0.5; lower = less weight on context).
    """
    return vectors.search_graph(
        query,
        k=k,
        type_filter=type,
        neighbors_per_seed=neighbors_per_seed,
        edge_factor=edge_factor,
    )


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
    parts.append("`body` is the freeform markdown content of the resulting note.")
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
