# brain-mcp

MCP server exposing the Obsidian second-brain vault to any MCP-capable client (Claude Code, Claude Desktop, OpenClaw, Cursor, …).

The MCP is **self-documenting**: doctrine, kind catalog, and workflow recipes are all served via tools. No client-side skills or CLAUDE.md tripwires required.

## Setup

```bash
cd ~/github/skalas/brain-mcp
uv sync
```

## Run (stdio, for local clients)

```bash
VAULT_PATH="/Users/skalas/Documents/Obsidian Vault" uv run brain-mcp
```

## Tools exposed

### Search & read

| Tool | Purpose |
|---|---|
| `search_notes(query, type?, updated_since?, k)` | substring search across `notes/`, `meetings/`, `conversations/`, `daily/` |
| `search_semantic(query, k, type?)` | vector search over note sections using local `multilingual-e5-large` embeddings |
| `search_hybrid(query, k, type?)` | reciprocal-rank fusion of `search_notes` + `search_semantic` |
| `read_note(id)` | full note content + parsed frontmatter |
| `list_index(name)` | dump a MOC (`people`, `projects`, `topics`, `timeline`, `tags`, `README`) |

### Doctrine & workflows

| Tool | Purpose |
|---|---|
| `get_doctrine()` | returns `_system/CLAUDE.md` — vault schema, write rules, language discipline. Call once per session before the first write. |
| `list_workflows()` | catalog of multi-step workflow recipes (non-kind) under `_system/recipes/` |
| `get_workflow(name)` | full body of a workflow recipe (e.g. `conversation-append-pass`, `dream-cycle`, `function-first-project-rewrite`) |
| `get_consolidation_policy()` | returns `_system/dream-policy.md` — the cross-agent rules every consolidation ("dream") pass must obey before writing: attribution, guardrails, dedup, propose-only. See [docs/dreamcore.md](docs/dreamcore.md). |
| `get_architecture()` | returns `_system/architecture.md` — ecosystem map + differentiated-ingestion registry (reference, not enforced). |

### Structured kinds

The vault declares **kinds** — typed entities with their own field schema and behavior — via recipe files under `_system/recipes/<kind>.md`. The MCP discovers them at startup and exposes per-kind tools dynamically.

| Tool | Purpose |
|---|---|
| `list_kinds()` | catalog of registered kinds (currently `book`, `recipe`, `task`) |
| `get_recipe(kind)` | full instructions for a kind: required fields, enrichment, side effects, body shape |
| `add_<kind>(data, body)` | create a new entity of `kind`. Validates `data` against the recipe schema and runs declared side effects. |
| `find_<kind>(where?)` | filter entries of `kind` (archive-class kinds only) |
| `update_<kind>(id, patch)` | patch fields on an existing entity (living-list kinds: `task`) |
| `complete_<kind>(id)` | flip state to the first terminal state (living-list kinds) |
| `list_<kind>(where?)` | list entries; terminal states excluded by default (living-list kinds) |

Kind tools carry their schema in the description — required fields, optional fields, valid `state` values, allowed filter keys. No client-side configuration needed.

### Unstructured writes

| Tool | Purpose |
|---|---|
| `append_section(id, body, date?)` | dated H2 append to an existing note + bump `updated:` + reindex |
| `create_note(type, slug, frontmatter, body)` | new untyped entity note (person/project/topic/ref) in `notes/` + reindex |
| `create_dated(kind, slug, body, frontmatter?, date?)` | new file in `daily/`, `meetings/`, or `conversations/` + reindex |

For structured entities, prefer `add_<kind>` — it enforces the recipe contract.

### Maintenance

| Tool | Purpose |
|---|---|
| `reindex_vectors(full?, note_id?)` | rebuild the vector index (per-note, full walk, or first-run bootstrap) |

## Recipe shape (declaring a new kind)

Drop a markdown file at `_system/recipes/<kind>.md` with this frontmatter:

```yaml
---
kind: book
class: archive               # or living-list
description: Short human description
target:
  type: ref                  # frontmatter type for resulting notes (person|project|topic|ref)
  slug_pattern: "book-{title-kebab}"
fields:
  required: [title, author]
  optional: [year, rating, themes]
retrieval:
  filters: [author, year, rating]
side_effects:
  - append_to: notes/books.md
# living-list only:
# states: [open, in-progress, done, snoozed]
# default_state: open
# terminal_states: [done]
---

# Recipe body — instructions for the model
```

Restart the MCP to pick up the new kind. The dynamic tools (`add_<kind>`, `find_<kind>`, etc.) appear automatically.

## Multi-agent coexistence (DreamCore)

Several agents (Claude Code, OpenClaw/Nico, Hermes) write to this one vault. They stay independent but inherit shared "dream"/consolidation decisions from **one place** — files in the vault, served by the tools above. Three roles:

- **Policy** (`get_consolidation_policy()`) — the invariants every agent must obey.
- **Instructivo** (`get_workflow("dream-cycle")`) — the canonical shape of a pass.
- **Map** (`get_architecture()`) — who ingests what, and where it goes.

Edit one file → every agent inherits it on its next run. No new server, no redeploy. See **[docs/dreamcore.md](docs/dreamcore.md)** for the full pattern and **[examples/](examples/)** for paste-ready templates.

## Vector search

Local-only semantic search via `sqlite-vec` + `fastembed` (`intfloat/multilingual-e5-large`, 1024-dim, multilingual ES/EN).

- Vectors live in `<repo>/.vectors.db` (gitignored — per-machine, regenerated via `reindex_vectors(full=True)`). Override with `BRAIN_VECTOR_DB`.
- Model defaults to `intfloat/multilingual-e5-large` (override with `BRAIN_EMBED_MODEL` / `BRAIN_EMBED_DIM`).
- Chunking is per H2 section; a preamble chunk includes title/aliases/tags so frontmatter is searchable.
- Writes auto re-embed the affected note; only sections whose content hash changed are re-encoded.
- First-run bootstrap: call `reindex_vectors()` once after install — walks the vault and embeds everything.

### CLI

```bash
# Embed the vault only if the DB is empty (idempotent).
VAULT_PATH="…" uv run brain-reindex --bootstrap

# Walk every note (and prune chunks for deleted notes).
VAULT_PATH="…" uv run brain-reindex --full

# Reindex a single note by id.
VAULT_PATH="…" uv run brain-reindex --note kavak-pricing-q1

# Drop the vector store and re-embed from scratch.
# Run once per machine after an embedding-model pooling/dimension change
# (e.g. fastembed switching multilingual-e5-large from CLS to mean pooling).
VAULT_PATH="…" uv run brain-reindex --rebuild
```

## Guardrails

- No delete tool. Deletion stays manual.
- No rename tool. Renames break wikilinks (which are by filename stem).
- Writes blocked inside `_system/`, `_index/`, `.obsidian/`.
- Every write runs `_system/scripts/reindex.sh` before returning.

## Wire to clients

**Claude Code** (recommended, user-scope so it's available from any project):

```bash
claude mcp add brain \
  --scope user \
  -e VAULT_PATH="$HOME/Documents/Obsidian Vault" \
  -- uv run --directory ~/github/skalas/brain-mcp brain-mcp
```

**Claude Desktop**: edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "brain": {
      "command": "uv",
      "args": ["run", "--directory", "/Users/<you>/github/skalas/brain-mcp", "brain-mcp"],
      "env": { "VAULT_PATH": "/Users/<you>/Documents/Obsidian Vault" }
    }
  }
}
```

**OpenClaw / Cursor / others**: same stdio command + `VAULT_PATH` env var. Any MCP 1.x-compatible client should work.

## Architecture

```
brain_mcp/
├── server.py     FastMCP tool registration. Generic tools + dynamic per-kind tools.
├── vault.py      Domain layer: Note parsing, path resolution, search, doctrine/workflow readers.
├── writes.py     Application layer: append_section, create_note, create_dated. Reindex on every write.
├── kinds.py      Recipe registry: parses _system/recipes/*.md into Kind objects.
├── kind_ops.py   Per-kind add/find/update/complete/list. Side-effect runner.
├── vectors.py    Local semantic-search index (sqlite-vec + fastembed).
└── cli.py        brain-reindex CLI.
```

Clean architecture: `kinds`/`vault` are pure domain; `kind_ops`/`writes` are application; `server` is presentation; `vectors` is infrastructure.

## Testing

```sh
uv pip install -e '.[dev]'
uv run pytest
```

Tests run against a throwaway vault (`tests/conftest.py` builds it under a temp dir and points `VAULT_PATH`/`BRAIN_VECTOR_DB` at it before import). They cover parsing, path-traversal/read-only guards, the link graph, connection queries, centrality, kinds/slugs, and write flows — and never load the embedding model, so the full suite runs in well under a second.
