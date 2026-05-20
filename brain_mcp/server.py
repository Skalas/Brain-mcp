"""brain-mcp: MCP server exposing the second-brain vault."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from . import vault, vectors, writes

mcp = FastMCP("brain")


@mcp.tool()
def search_notes(
    query: str,
    type: str | None = None,
    updated_since: str | None = None,
    k: int = 10,
) -> list[dict]:
    """Search vault notes by substring across aliases, ids, and body.

    Args:
        query: text to look for (case-insensitive).
        type: optional frontmatter type filter (person, project, topic, ref, meeting, daily, conversation).
        updated_since: optional YYYY-MM-DD; only include notes whose `updated` >= this date.
        k: max results.
    """
    return vault.search_notes(query, type, updated_since, k)


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
def append_section(id: str, body: str, date: str | None = None) -> dict:
    """Append a dated H2 section to an existing note.

    Args:
        id: note id (filename stem). Note must already exist.
        body: markdown body for the new section (no need to include the `## YYYY-MM-DD` header — it's added).
        date: optional YYYY-MM-DD; defaults to today.
    """
    return writes.append_section(id, body, date)


@mcp.tool()
def create_note(
    type: str,
    slug: str,
    frontmatter: dict,
    body: str,
) -> dict:
    """Create a new note in notes/. Fails if slug already exists.

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


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
