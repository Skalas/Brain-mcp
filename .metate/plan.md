# Discover plan — brain-mcp logic & retrieval sweep

_Source: session logic-review of `vectors.py` + `vault.py`, plus two read-only design
sweeps over the write/kind/tool layer and the vault's `_system` doctrine
(769 active notes; largest note ~356 KB; 111 notes with zero H2)._

This is a **slate**, ranked by value and failure-surface (never dev time). Prep picks the
sprint(s), finalizes the mode, and files the issue ledger from the chosen candidate(s).

---

## Candidate 1 — Retrieval recall & correctness  `[HOLD]`  ★ recommended first

**Goal.** Close the silent recall holes and correctness bugs in the semantic/hybrid/graph
search stack. Cohesive (all in `brain_mcp/vectors.py` + one constant), and forces a
re-embed that Candidate-1's chunk change needs anyway. This is the core job of the brain —
retrieve and connect — and every downstream ranking improvement is capped by it.

Bundles three findings:

**(A1) No chunk-size ceiling — richest notes are gutted at embed time.**
`MIN_CHUNK_CHARS` guards the floor; there is no upper bound. `multilingual-e5-large`
truncates to ~512 tokens (~2 KB). Notes with few/no H2 (111 notes are a single body chunk)
and huge sections only ever embed their opening ~2 KB; the rest is invisible to semantic
search.

**(A3) Connective signal dormant at default weight.**
`search_hybrid` default `structural_weight=0.1` leaves a pure-graph bridge note's normalized
PPR score fighting 0.1 against text hits at up to 1.0, so the headline "surface a note that
sits between several strong hits" capability almost never fires unless the caller raises the
weight manually.

**(A4) Three search correctness/determinism bugs.**
- `search_semantic` returns fewer than `k` when no `type_filter`: dedup-by-`note_id` shrinks
  results but over-fetch only applies under a filter (`vectors.py:290`).
- `search_graph` neighbor pick is non-deterministic (frozenset iteration order varies with
  `PYTHONHASHSEED`) and unranked — arbitrary neighbors survive truncation (`vectors.py:510`).
- `search_graph` can return up to `2k` results: graph neighbors capped at `k`, then
  concatenated with up to `k` seeds, total untruncated (`vectors.py:546`).

**Blast-radius.** `vectors.py` (`chunk_note`, `search_semantic`, `search_graph`,
`_CONNECTIVE`/`structural_weight` default); requires `rebuild_all` re-embed.

**Seed DoD + test matrix.**
- T1  A section/preamble/headingless body over a max-chars ceiling is sub-split
  (H3 → paragraph → sliding window w/ overlap) into multiple `section_idx` sub-chunks.
- T2  Content past the first ~2 KB of a large note is retrievable via `search_semantic`.
- T3  A pure-graph connective note (matches neither text nor embedding) reliably enters
  top-k at the new default weight; regression: `structural_weight=0` still gives pure-text order.
- T4  A query hitting a multi-H2 note still returns `k` distinct notes when the corpus has ≥k.
- T5  `search_graph` neighbor selection is stable across two fresh interpreters and cut by a
  defined key (degree/centrality/alpha).
- T6  `search_graph` respects a documented `k` contract (≤k, or 2k documented as intentional).

**Minor add-on.** Replace `assert new_id is not None` in `reindex_note` with a raise
(survives `python -O`).

---

## Candidate 2 — Real lexical arm: FTS5 / BM25  `[EXPAND]`

**Goal.** Replace whole-query substring matching with a real term-level ranker. `search_notes`
does `body_lower.find(query_lower)` — a multi-word query matches only if it appears literal
and contiguous, so the RRF "keyword" arm that should catch proper nouns and rare terms barely
fires. SQLite FTS5 ships alongside sqlite-vec.

**Blast-radius.** New FTS5 virtual table over note bodies; `search_notes` + `_fuse_rrf`
(third RRF input); reindex path.

**Seed DoD + test matrix.**
- T1  FTS5 table built and kept in sync with note bodies on reindex.
- T2  Multi-term query ranks a note containing all terms non-contiguously above one with a
  single term (BM25 ordering).
- T3  Proper-noun / rare-term query outranks embedding-only noise.
- T4  Lexical arm fused as a third RRF input without regressing existing hybrid tests.

---

## Candidate 3 — Reindex batching / deferral  `[HOLD]`

**Goal.** Stop paying O(vault) synchronous MOC rebuilds on every interactive write. Each
`append_section`/`create_note`/`create_dated`/`update`/`edit_note` shells out to the full
`reindex.sh`; five task adds = five full rebuilds. Worse, `archive_note(strip_refs=True)`
reindexes *inside* the per-referrer loop (N full rebuilds). Compounds with vault size — the
most likely cause of the brain feeling slow.

**Blast-radius.** `writes.run_reindex` and every write op; `kind_ops.update`.

**Seed DoD + test matrix.**
- T1  Interactive writes do cheap vectors-only update; expensive MOC regen is deferred
  (dream/cron) or debounced.
- T2  `strip_refs` strips all referrers, then reindexes once (not per-ref).
- T3  No stale-index regression for the immediate read-after-write path (coordinate w/ Cand. 4).

---

## Candidate 4 — Lifecycle: edit frontmatter + rename/merge with backlink rewrite  `[EXPAND]`

**Goal.** Upgrade from capture-only to a maintainable brain. Today there is **no tool** to
change an archive-kind or base-note field after creation (`edit_note` refuses frontmatter and
points to `update_<kind>`, which only exists for living-list kinds), and no rename/merge — a
renamed person or duplicate subject can only be archive+recreated, orphaning inbound backlinks.
The backlink-safe rewrite machinery already exists inside `archive_note`
(`find_references` + `sub_outside_code`).

**Blast-radius.** `kind_ops` (extend `update` to archive kinds), `writes`
(new `rename_note` / `merge_note` + generic frontmatter patch).

**Seed DoD + test matrix.**
- T1  Generic frontmatter patch works on base notes and archive-kinds (validated where a
  recipe declares fields).
- T2  `rename_note` moves the file and rewrites all inbound wikilinks (incl. `#heading`/`|alias`
  forms), leaving no dangling refs and touching no code spans.
- T3  `merge_note` folds one note into another, repointing backlinks, then archives the source.

---

## Candidate 5 — Decide the kinds boundary + tool-surface strategy  `[strategic — prep sets mode]`

**Goal.** Resolve two coupled design calls before more kinds are added, because filters, field
typing, and side-effect indexes are all downstream of them.

**(D1) Core entities are schema-less.** `book`/`task` are typed with validation and dedicated
tools, but `person`/`project` — what a second brain lives on — are created via `create_note`
accepting any frontmatter dict with zero validation (schema lives only as prose in
`get_doctrine`). The "why is book a kind but person not" boundary is arbitrary.

**(D2) Per-kind tools scale against the abstraction's premise.** 5 kinds already mint ~14 tools
atop ~18 static; LLM tool-selection accuracy degrades and the descriptions already carry
defensive anti-confusion patching.

**Blast-radius.** `writes.create_note`, `kinds.py`, `kind_ops` (filters/validation),
`server.py` (tool registration), `get_doctrine` (prose → code).

**Seed DoD + test matrix.**
- T1  Decision recorded: promote person/project to kinds **or** scope kinds narrowly + add
  lightweight per-type frontmatter validation to `create_note`.
- T2  Threshold defined for collapsing per-kind tools into generic
  `add_entity(kind, data, body)` / `list_entities(kind, where)`.
- T3  (if typing) recipes can declare field type/enum; `add`/`update` validate it.
- T4  (if typing) filters support comparison operators (`{due: {before}}`, `{rating: {gte}}`).

---

## Backlog — real but refines something already working; do later

- **Graph hygiene** — daily/conversation/dream notes are first-class undirected graph nodes and
  act as artificial high-degree bridges, distorting PPR proximity and `path_between`. Exclude
  or down-weight them from the association graph while keeping them searchable.
- **Dangling links as growth signal** — aggregate "targets referenced by many notes with no
  file yet" as note-creation proposals for the dream REM stage.
- **Side-effect indexes rot** — `notes/books.md` etc. append-only, never reconciled; make them
  derived (regenerated from kind queries on reindex) instead of incrementally appended.
- **Write→index staleness** — writes and the vector store are coupled only by the doctrine's
  instruction to run `reindex.sh`; expose an index-freshness marker (vault sig vs last-indexed)
  or fire `reindex_note` from the write tools directly.
- **`section_idx` positional** — inserting an H2 mid-note shifts every later index and forces
  needless re-embeds; a heading-derived stable key fixes it.
- **Semantic dedup keeps one chunk/note** — no multi-section evidence aggregation, no recency
  prior despite a densely timestamped vault; only `type_filter` exposed (no tag/context/status).
- **Duplicate-title `add` hard-fails** — deterministic `kind-{title-kebab}` slug collides on
  repeated short titles; auto-suffix or date-scoped slugs for living-list kinds.
- **Concurrent full-file body writes** — last-writer-wins on note bodies despite the
  multi-agent design; only index appends are concurrency-safe. Document, or read-merge before
  overwrite on appends.
- **Terminology overload** — `class: archive` (durable active note) collides with `_archive/`
  and `archive_note` (retiring a note); "recipe" means kind-def, food-kind, and workflow.
