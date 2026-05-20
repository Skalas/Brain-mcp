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
| `read_note(id)` | full note content + parsed frontmatter |
| `list_index(name)` | dump a MOC (`people`, `projects`, `topics`, `timeline`, `tags`) |
| `append_section(id, body, date?)` | dated H2 append to an existing note + bump `updated:` + reindex |
| `create_note(type, slug, frontmatter, body)` | new note in `notes/` + reindex |
| `create_dated(kind, slug, body, frontmatter?, date?)` | new file in `daily/`, `meetings/`, or `conversations/` + reindex |

## Guardrails

- No delete tool. Deletion stays manual.
- No rename tool. Renames break wikilinks.
- Writes blocked inside `_system/`, `_index/`, `.obsidian/`.
- Every write runs `_system/scripts/reindex.sh` before returning.

## Wire to clients

Claude Code: see project-level `.mcp.json` in the vault.

Claude Desktop: edit `~/Library/Application Support/Claude/claude_desktop_config.json`.
