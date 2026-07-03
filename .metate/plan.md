# Discover plan — read-path section retrieval (+ cosmetic cleanup)

_Chosen: Candidate 1 (`[HOLD]`) merged with Candidate 4 (`[REDUCE]`). Sources: the S1
`live-test:orbis-bos` signal + open issue #37, bundled with the 5 S3 in-diff signals parked
from last sprint's review — all resident in `brain_mcp/vectors.py`, the file this sprint
already opens. Prep finalizes the sprint mode and files the issue ledger._

The previous sprint (retrieval-recall-correctness, #45–#50) fixed **findability** — deep
sub-chunks are now embedded, ranked, and returned at their `section_idx`. It exposed the
**readability** gap: the brain can locate the right slice but has no way to *serve* it.

---

## Part A — Read-path: section-level retrieval  `[HOLD]`  ★ core

**Goal.** Let a caller read exactly the sub-chunk that search found, without pulling the whole
note. Today `search_semantic` returns a deep `section_idx` (e.g. 40002 on `orbis-bos`, 351 KB)
carrying only a ~240-char snippet + heading; `read_note` returns the entire note as one body.
In the live post-ship test the correct answer was only obtainable after `read_note(351 KB)` +
manual Bash grep / Python over `.claude/` tool-result caches — i.e. **the read half happened
outside brain-mcp entirely**. This completes last sprint: findability without readability is
useless. Closes signal `live-test:orbis-bos` (S1, out-of-diff) and open issue #37
("vectors for dedup, concatenation for use").

**Blast-radius.** `server.read_note` (server.py:37–49, small), `vectors._snippet` /
`vectors._section_indices`, and the search result shape (locator returned to the caller).
Read-only additive surface — no re-embed required.

**Seed DoD + test matrix.**
- T1  A `read_section(note_id, section_idx)` (or `read_note(section=…)`) returns just that
  sub-chunk's body — heading + full section text, not the 240-char snippet, not the whole note.
- T2  Search results (`search_semantic` / `search_hybrid`) carry a locator sufficient to fetch
  the exact section they matched (the `section_idx` round-trips back into T1's reader).
- T3  Reproduce `orbis-bos`: the deep-section answer is retrievable end-to-end **inside**
  brain-mcp — no Bash grep / Python on cache files needed.
- T4  Locators are stable against the sub-`section_idx` keys introduced last sprint (H3 →
  paragraph → sliding-window sub-chunks resolve to a readable slice).
- T5  Out-of-range / unknown `section_idx` returns a clean domain error, not a truncated body
  or a stack trace.

---

## Part B — Cosmetic cleanup: dead branches + named tunables  `[REDUCE]`

**Goal.** Clear the 5 S3 in-diff signals parked from last sprint's review. All live in
`brain_mcp/vectors.py` — the file Part A already opens, with tests warm. No behavior change;
this is hygiene that rides along near-free and empties the signal queue instead of letting it
rot another cycle.

**Blast-radius.** Low, confined to `vectors.py`; gate must stay green with no test-count delta.

**Seed DoD + test matrix.**
- T1  `search_semantic` over-fetch factor `k * 4` promoted to a named module constant, matching
  the module's existing `_CONNECTIVE_FACTOR` / `_RRF_K` / `_SUB_IDX_SCALE` / `_SPLIT_OVERLAP` /
  `_MAX_SUB_CHUNKS` convention. (signal `review:R1-design`)
- T2  Unreachable `else: _sliding_window(...)` fallback in `_split_oversized` removed or reframed
  as an assertion (every producer already guarantees output ≤ limit). (signal `review:R1-design`)
- T3  `_neighbor_key` closure in `search_graph` hoisted out of the `for seed in seeds:` loop (or
  made module-level) — it closes over nothing loop-variant. (signal `review:R1-design`)
- T4  `num_parts >= _SUB_IDX_SCALE` (≥1000) guard in `_section_indices` annotated as not
  load-bearing on the production path (the 512 cap truncates first), or restructured so its
  reachability is honest. (signal `review:R2-security`)
- T5  `if len(seed_list) > k` branch in `search_graph` — annotate as a defensive guard against a
  future `search_hybrid` contract change, or remove as currently unreachable. (signal
  `review:extra-verify`)

---

## Explicitly out of scope (queued for a later discover)

- **Reindex batching / deferral** `[HOLD]` — the likely cause of the brain feeling slow; own
  sprint (write path).
- **FTS5 / BM25 lexical arm** `[EXPAND]` — real term-level keyword ranker; own sprint (new
  virtual table).
- **Lifecycle: edit frontmatter + rename/merge** `[EXPAND]` — capture-only → maintainable brain.
- **Kinds boundary + tool-surface strategy** `[strategic]` — a decision, not a sprint.
- **Graph viz (#44)** — backlog, blocked on graph-hygiene landing first.
