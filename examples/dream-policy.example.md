---
# Example coexistence policy. Drop at _system/dream-policy.md and adapt.
# The frontmatter is the machine-readable enforceable subset; the prose explains it.
policy_version: 1
policy_date: "2026-01-01"
agents:
  - "Claude Code"   # a CLI coding agent
  - "Nico"          # an orchestrator
  - "Hermes"        # a chat agent
heading_signature: "## {date} — {agent}"   # date = YYYY-MM-DD; agent ∈ agents
ingest_boundaries:                          # each agent scans ONLY its own sources
  "Claude Code": ["~/.claude/projects/", "~/.cursor/projects/"]
  "Nico":        ["~/.your-orchestrator/sessions/"]
  "Hermes":      ["~/.your-chat-agent/"]
protected_paths: ["_system/", "_index/", ".obsidian/"]   # REFUSE — never written
guardrail_slug_prefixes: ["legal-", "financial-", "health-"]
guardrail_slugs_exact: ["wedding"]
max_stale_days: 7
---

# Consolidation coexistence policy

The single source of shared rules for the independent consolidation agents that
write to the same vault. This is a POLICY, not a procedure — each agent runs its
own pipeline and obeys these invariants. Load it via
`get_consolidation_policy()` (or read this file directly if non-MCP) once per run,
before writing.

## 1. Section attribution

Every section is attributable to its author, in ONE of two forms:

- **Agent signature** — `## YYYY-MM-DD — <Agent>` (direct/conversational writes).
- **Source marker** — `<!-- src:<source>:<id> -->` in the section body, for
  pipeline-ingested appends (the heading text is then a descriptive title).

An agent edits only sections it owns; it never modifies another's. Conflicts are
resolved by appending a new section. Writes are append-only.

## 2. Guardrail domains

A target whose slug matches `guardrail_slugs_exact`, or starts with any
`guardrail_slug_prefixes`, is **propose-only** — never auto-applied, in any mode.
`protected_paths` are REFUSE.

## 3. Durable vs ephemeral

Only durable content is consolidated. When unsure → ephemeral. For coding sources,
judge by session **mode** (`discussion | execution | mixed`), not transcript
length: `execution` yields at most its one decision; `discussion`/`mixed` digest
in full.

## 4. Shared databases (if any)

Concurrency safety by idempotent writes on a deterministic key (`INSERT OR
IGNORE`), never locks. No agent updates/deletes a row it didn't originate. Keep
sensitive DBs (finance/health) OFF synced storage.

## 5. Recombination is propose-only

Proposing new cross-links or new notes is always propose-only. The only auto-apply
path is appending an attributed section to an already-existing note.

## 6. Ingestion boundaries

Each agent scans only `ingest_boundaries[self]`. Shared state is reached only
through the vault and any shared DB, under the rules above.

## Versioning & degradation

`policy_version` is recorded per RUN (in the agent's journal/ledger), not stamped
per section. Unreadable policy → FAIL CLOSED. Stale (older than `max_stale_days`)
→ proceed PROPOSE-ONLY.
