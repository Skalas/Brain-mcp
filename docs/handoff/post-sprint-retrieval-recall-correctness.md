# Post-sprint handoff — retrieval-recall-correctness

**Branch:** `feat/retrieval-recall-correctness` · **Base:** `main` · **Mode:** HOLD
**Closed:** 2026-07-02 · **Issues:** #45–#50 (T1–T6)

## What shipped

Correctness + recall hardening of the semantic/hybrid/graph search stack, all in
`brain_mcp/vectors.py` (+ tests). Gate green in-env: `ruff` · `mypy` (0 issues) · `pytest` (95).

| T | Change |
|---|--------|
| T1 | `MAX_CHUNK_CHARS` ceiling; oversized sections sub-split (H3 → paragraph → sliding window w/ overlap) into stable sub-`section_idx` keys. `assert`→`raise` in `reindex_note`. |
| T2 | Content beyond the model's ~512-token limit is now embedded/retrievable (proven on real data — see below). |
| T3 | Default `structural_weight` 0.1 → 0.3 (in `vectors.py` + `server.py`) so connective/bridge notes actually surface. |
| T4 | `search_semantic` over-fetches `k*4` regardless of `type_filter`, so dedup-by-note never returns < k. |
| T5 | `search_graph` neighbor selection deterministic + ranked by `(-degree, id)` (was `frozenset`-order, PYTHONHASHSEED-dependent). |
| T6 | `search_graph` respects the `k` contract; **seeds are never evicted by graph-only neighbors** (membership), and results are score-descending (presentation), consistent with the other search fns. |

Hardening surfaced during review (rounds 1–3 + extraordinary round):
- Chunk-explosion fix: header blob capped (`_MAX_HEADER_CHARS`), sliding-window overlap made relative to limit, per-section cap `_MAX_SUB_CHUNKS=512` and per-note cap `_MAX_CHUNKS_PER_NOTE=2048`, both logging on truncation.
- `reindex_all` resilience: per-note try/except with `conn.rollback()` on failure, `failed[]` in the returned totals, `note_id` in guard errors — one bad note no longer aborts the whole walk or skips prune.

## Verified on real data

Full re-embed of the live vault (`brain-reindex --rebuild`): **769 notes, 5832 chunks, 0 failed, ~16.7 min**. **2632 chunks (~45%) are sub-chunks of split sections** — content that was silently truncated at embed time before this sprint. Live check: a phrase from a deep sub-chunk of `orbis-bos` (351 KB → 434 chunks) is retrieved by `search_semantic` at score ~0.81; it was invisible before.

## Operational note (required on every machine)

The chunking change only benefits an **existing** vault after a re-embed:
`VAULT_PATH=… uv run brain-reindex --rebuild`. The `.vectors.db` is per-machine/gitignored.

## Gate invocation (fixed this sprint)

Gates must run **in-env**: `uv run --extra dev {ruff check .|mypy .|pytest}`. `uvx`/bare `mypy .`
uses an isolated env without the project's deps/stubs (types-PyYAML, mcp) and is **falsely red**.
`.metate/profile.yml` `fastGate`/`shipGate` were corrected accordingly.

## Deferred debt (captured, not fixed)

Cosmetic/report-only findings parked in `.metate/signals.json` (S3, in-diff, `open`) for the next
`discover` to triage/promote:
- `k*4` inline literal → named constant.
- Unreachable fallback branch in `_split_oversized`.
- `_neighbor_key` closure redefined per loop iteration (hoist).
- `num_parts >= _SUB_IDX_SCALE` guard is dead-code on the production path (512 cap truncates first).
- `if len(seed_list) > k` branch in `search_graph` unreachable today.

## Next-sprint pointers (primary input to the next discover)

- **Candidates 2–5** in `.metate/plan.md` remain unstarted, ranked: (2) FTS5/BM25 real lexical arm
  `[EXPAND]`; (3) reindex batching/deferral `[HOLD]`; (4) lifecycle — edit frontmatter + rename/merge
  with backlink rewrite `[EXPAND]`; (5) decide the kinds boundary + tool-surface strategy `[strategic]`.
- **Node/graph visualization** (better than Obsidian's, PPR-weighted/typed) tracked as backlog issue
  **#44** — depends on graph hygiene (down-weighting daily/conversation bridge nodes) landing first.
- Backlog refinements in `.metate/plan.md`: graph hygiene, dangling-links-as-growth-signal,
  side-effect index reconciliation, write→index staleness marker, positional `section_idx`.
