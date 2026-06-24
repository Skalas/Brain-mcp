# Live smoke tests (through the MCP transport)

The pytest suite (`uv run pytest`) proves the logic offline. This card proves the
**wiring** — that the tools are registered and reachable through the actual MCP
server a client launches. Run these in a **fresh** Claude Code session or after a
desktop-app restart (the server is spawned per session via
`uv run --directory <repo> brain-mcp`, so a new session picks up the latest code
on `main` automatically — no manual update step).

Each test is a prompt to type, plus what a **pass** looks like. Expected values
are grounded in the vault as of this writing; the *shapes* are what matter if the
data has shifted.

---

## 1 — Graph-expanded retrieval (`search_graph`)

> Use the brain `search_graph` tool for 'pricing' with k=5. Show me which results are seeds vs graph neighbors.

**Pass:** results carry `source: "seed"` (direct hits like `pricing`,
`kavak-pricing-q1`) **and** `source: "graph"` neighbors (e.g. `okrs`, `finance`),
each graph result with a `neighbor_of` field. Neighbor scores sit **below** their
seeds (decayed by `edge_factor=0.5`); a 2nd-degree neighbor (e.g. `finance` via
the Land Cruiser note) scores lowest.

## 2 — Link graph (`links_of`)

> Call brain `links_of` for the note `pricing`.

**Pass:** `{outbound: [...], inbound: [...]}`. Outbound includes `okrs` /
`pricing-19-oct-22` each with a `dangling` boolean; inbound lists the notes that
wikilink to it. `read_note("pricing")` should also now include a `links` block.

## 3 — Connection queries (`neighborhood`, `path_between`)

> Use brain `neighborhood` on `goes` at depth 1, then `path_between` `pricing` and `goes`.

**Pass:** `neighborhood` returns `{nodes, edges, truncated}` — for the `goes` hub,
`truncated: true` (radius-1 exceeds the 100-node cap). `path_between` returns
`connected: true` with a short `path` list (or `connected: false`, `path: []` when
two notes genuinely don't link).

## 4 — Security: path-traversal guards 🔒

> Call brain `read_note` with id `../../../../etc/hosts`.

**Pass:** rejected server-side — `Note '../../../../etc/hosts' not found in
notes/, daily/, meetings/, or conversations/`. No file contents leak. This
message comes from our code (`find_note_by_id` + `SAFE_STEM_RE`), so it confirms
the **server** guard, not the harness.

> Call brain `restore_note` with id `../../../../tmp/whatever`.

**Pass:** rejected with `Invalid note id '../../../../tmp/whatever'` (a
`VaultError` from `restore_note`, guard added in 1ba969a).

> ⚠️ Do **not** use `../_system/CLAUDE` as the restore payload to test the server
> guard: the harness self-modification classifier denies it *before* the tool
> runs (it targets agent-instruction config), so the server guard is masked and
> you can't tell which layer rejected it. Use a neutral traversal target like
> `../../../../tmp/whatever` to exercise our code directly. The `restore_note`
> guard also has unit coverage: `test_security.py::test_restore_note_rejects_traversal`.
