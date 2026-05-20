# brain-mcp

MCP server exposing the Obsidian second-brain vault to any MCP-capable client.

## Setup

```bash
cd ~/Documents/brain-mcp
uv sync
```

## Run (stdio, for local clients)

```bash
VAULT_PATH="/Users/skalas/Documents/Obsidian Vault" uv run brain-mcp
```

## Tools exposed

| Tool | Purpose |
|---|---|
| `search_notes(query, type?, updated_since?, k)` | grep-based search across `notes/`, `meetings/`, `conversations/`, `daily/` |
| `search_semantic(query, k, type?)` | vector search over note sections using local `multilingual-e5-large` embeddings |
| `search_hybrid(query, k, type?)` | reciprocal-rank fusion of `search_notes` + `search_semantic` |
| `read_note(id)` | full note content + parsed frontmatter |
| `list_index(name)` | dump a MOC (`people`, `projects`, `topics`, `timeline`, `tags`) |
| `append_section(id, body, date?)` | dated H2 append to an existing note + bump `updated:` + reindex |
| `create_note(type, slug, frontmatter, body)` | new note in `notes/` + reindex |
| `create_dated(kind, slug, body, frontmatter?, date?)` | new file in `daily/`, `meetings/`, or `conversations/` + reindex |
| `reindex_vectors(full?, note_id?)` | rebuild the vector index (per-note, full walk, or first-run bootstrap) |

## Vector search

Local-only semantic search via `sqlite-vec` + `fastembed` (`intfloat/multilingual-e5-large`, 1024-dim, multilingual ES/EN).

- Vectors live in `<repo>/.vectors.db` (gitignored — per-machine, regenerated via `reindex_vectors(full=True)`). Override with `BRAIN_VECTOR_DB`.
- Model defaults to `intfloat/multilingual-e5-large` (override with `BRAIN_EMBED_MODEL` / `BRAIN_EMBED_DIM`).
- Chunking is per H2 section; a preamble chunk includes title/aliases/tags so frontmatter is searchable.
- Writes (`append_section`, `create_note`, `create_dated`) automatically re-embed the affected note; only sections whose content hash changed are re-encoded.
- First-run bootstrap: call `reindex_vectors()` once after install — walks the vault and embeds everything (a few minutes the first time; the model is cached locally afterward).

### CLI

```bash
# Embed the vault only if the DB is empty (idempotent — safe to run anywhere).
VAULT_PATH="…" uv run brain-reindex --bootstrap

# Walk every note (and prune chunks for deleted notes).
VAULT_PATH="…" uv run brain-reindex --full

# Reindex a single note by id.
VAULT_PATH="…" uv run brain-reindex --note kavak-pricing-q1
```

## Guardrails

- No delete tool. Deletion stays manual.
- No rename tool. Renames break wikilinks.
- Writes blocked inside `_system/`, `_index/`, `.obsidian/`.
- Every write runs `_system/scripts/reindex.sh` before returning.

## Wire to clients

Claude Code: see project-level `.mcp.json` in the vault.

Claude Desktop: edit `~/Library/Application Support/Claude/claude_desktop_config.json`.
