# Dream cycle — how to build a conformant consolidation pass

The canonical procedure any agent follows to turn its own sessions into durable
vault knowledge. This is the *instructivo* — the shape of the process, not each
agent's implementation (source discovery stays local) and not the law (invariants
live in the policy). Drop at `_system/recipes/dream-cycle.md`; served by
`get_workflow("dream-cycle")`.

## When to use

Standing up or reviewing a dream pass for any agent, or whenever you change how a
pass runs and need it to stay compatible with the others.

## Before anything: load the law

Call `get_consolidation_policy()` (or read `_system/dream-policy.md` if non-MCP).
Bind `policy_version`, `ingest_boundaries[self]`, guardrails, `protected_paths`,
`max_stale_days`. Unreadable → fail closed; stale → propose-only.

## The three-stage contract

A stage split is real only if it changes context shape, model tier, or write
permission. These three do:

- **Light** — intake/signal. Dedup & merge the day's fragments. Never writes the
  durable graph.
- **Deep** — distillation. The single writer. Appends attributed sections to
  existing notes.
- **REM** — recombination. Propose-only cross-links and new-note stubs.

## The phases

0. Load policy.
1. **Stage** — discover only your own sources (LOCAL). For coding sources, tag each
   session `discussion | execution | mixed`.
2. **Triage** — durable vs ephemeral; when unsure, ephemeral.
3. **Consolidate (Deep)** — append attributed sections (signature or `src:` marker).
   Guardrail targets are propose-only. `execution` → the decision only;
   `discussion`/`mixed` → full digest.
4. **REM** — propose only; no writes to other notes' frontmatter/links.
5. **Journal** — record `policy_version`, counts, anything held for review.
6. **Ledger** — mark processed in YOUR shard; never rewrite another's.

## Local vs shared

- Local: source discovery + parsing, model tiers, journal format, scheduling.
- Shared (consume, don't copy): the policy, this instructivo, the ledger contract,
  the attribution convention.

## New-agent checklist

- [ ] Register identity + ingest boundary in the policy.
- [ ] Load the policy at run start; stamp `policy_version`.
- [ ] Deep is your only writer; attribute every section; REM is propose-only.
- [ ] Write processed keys to your own ledger shard.
- [ ] Verify: bump `policy_version` → next run picks it up with no code change.
