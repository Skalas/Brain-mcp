# Post-sprint handoff — read-path-section-retrieval

**Branch:** `feat/read-path-section-retrieval` · **Base:** `main` · **Mode:** HOLD
**Closed:** 2026-07-02 · **Issues:** #52–#57 (T1–T5 + cleanup umbrella)

## What shipped

The **read half** of retrieval. Last sprint made deep sub-chunks *findable*; this one makes
the located slice *readable* without pulling the whole note. Gate green in-env: `ruff` ·
`mypy` (0 issues) · `pytest` (101, +6 new).

| T | Change |
|---|--------|
| T1 | New `read_section(id, section_idx)` — MCP tool (`server.py`) + `vectors.read_section` — returns the exact chunk that was embedded/scored (heading + full section body), read straight from the `chunks` table by the `UNIQUE(note_id, section_idx)` key. Not the 240-char snippet, not the whole note. |
| T2 | `section_idx` locator now propagates through `search_hybrid` / `search_graph`: `_fuse_rrf` carries the semantic hit's `section_idx`; grep-only / graph-only (whole-note) hits carry `None`. The idx a hit reports round-trips into `read_section`. |
| T3 | End-to-end read verified on the **live vault**: `orbis-bos` (351 KB, 425 sub-chunks) deep sections read back through `read_section` — the extraction that the S1 signal did via Bash grep on cache files now happens inside the tool. |
| T4 | Sub-`section_idx` keys from last sprint's splitter (`(base+1)*_SUB_IDX_SCALE + i`) resolve via `read_section`; live index confirms (top note 425 sub-chunks, max idx 153002). |
| T5 | Unknown `note_id` **or** unknown `section_idx` → clean `ValueError` (matches `read_note`'s style), never a truncated body or unhandled exception. |

**Part B — cleanup (REDUCE), no behavior change** (closed the 5 S3 in-diff signals from the
prior sprint's review):
- `k*4` over-fetch → named `_OVERFETCH_FACTOR`.
- `_split_oversized` dead `else` re-split → hard `RuntimeError` backstop (raise, not assert,
  so it survives `python -O` and matches `_section_indices`' guard style; review-round design
  finding folded in).
- `_neighbor_key` closure hoisted out of the seed loop.
- `_section_indices` `>=_SUB_IDX_SCALE` guard and `search_graph`'s `len(seed_list) > k` guard
  annotated as not load-bearing on the production path (kept as defensive invariants).

## Verified on real data

The live `.vectors.db` (769 notes / 5832 chunks from last sprint's rebuild) needs **no
re-embed** — `read_section` reads existing chunks. `read_section("orbis-bos", 2000)` returns a
418-char deep section; T5 error path returns clean `ValueError` on the real DB. The full
`search → read_section` round-trip via the running MCP server requires a **server restart** to
pick up the new tool (the only step tests can't self-certify).

## Operational note

No migration or re-embed required — this sprint is read-path only, additive, and reads the
existing index. Restart the `brain` MCP server so clients see the new `read_section` tool.

## Deferred debt (captured, not fixed)

- **[review:elegance] Test embed-stub duplication.** `_query_embed` + the query/passage
  monkeypatch lambda are now copied across `test_read_section.py`, `test_search_semantic.py`,
  and `test_chunking.py`. **Trigger:** a 4th copy appears (next test that stubs `_embed`) →
  extract a shared `install_marker_embed(monkeypatch, marker)` fixture into `conftest.py`.
  (`techDebtFile` unset in profile — recorded here instead.)

## Next-sprint pointers (primary input to the next discover)

- **Candidates 2–5** from the prior slate remain unstarted, ranked: (2) reindex
  batching/deferral `[HOLD]` — the likely cause of the brain feeling slow; (3) FTS5/BM25 real
  lexical arm `[EXPAND]`; (4) lifecycle — edit frontmatter + rename/merge with backlink rewrite
  `[EXPAND]`; (5) kinds boundary + tool-surface strategy `[strategic]`.
- **Issue #37** (area-slice context assembly — vectors for dedup, concatenation for use): the
  read primitive now exists; the follow-on is *aggregating* multiple sections/notes into one
  assembled context. Consider promoting alongside a `read_section`-batch or multi-section read.
- **Node/graph visualization (#44)** — still backlog, still gated on graph-hygiene landing.
- **Read-path follow-ons worth watching:** a multi-section batch read (read N locators at once)
  and exposing `section_idx` in `search_notes`/grep results (currently semantic-only).
