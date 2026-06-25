# DreamCore — multi-agent coexistence over one vault

How several independent agents consolidate into a single Obsidian vault without
corrupting each other's work — and inherit shared decisions from one place.

> Narrative version: ["Don't Build a Second Brain"](https://skalas.me/dont-build-a-second-brain).
> Paste-ready templates: [`../examples/`](../examples/).

## The problem

Multiple agents — a CLI coding agent (Claude Code), an orchestrator (OpenClaw/Nico),
a chat agent (Hermes) — each run their own nightly "dream" pass that distills the
day's sessions into the vault. Three passes meant three copies of the rules they
had to agree on (how to sign an entry, what's off-limits, how to dedup), and the
copies drifted.

## The wrong fix

Build a second MCP server — a "dreamer" — that runs consolidation for everyone.
It overlaps the vault server you already have, and it pulls staging (a plain file
write) behind a network call. You'd be adding a system to remove duplication.

The thing duplicated isn't the engine. It's the **decisions**.

## The model: decisions as data, served by the brain

Keep one MCP server (this one). Add the shared decisions as **files in the vault**,
each served by a thin reader tool. Three roles, kept separate on purpose:

| Role | File | Tool | Nature |
|---|---|---|---|
| **Policy** — the law | `_system/dream-policy.md` | `get_consolidation_policy()` | enforced invariants |
| **Instructivo** — the how | `_system/recipes/dream-cycle.md` | `get_workflow("dream-cycle")` | canonical procedure |
| **Map** — the territory | `_system/architecture.md` | `get_architecture()` | reference |

Each tool is ~3 lines:

```python
@mcp.tool()
def get_consolidation_policy() -> str:
    """The cross-agent rules every dream pass must obey."""
    return (VAULT / "_system" / "dream-policy.md").read_text()
```

Each agent runs its **own** pass on its **own** sources. It just reads the rulebook
before writing. Editing one file changes how every agent behaves — on its next run,
with no code change.

## Two distribution channels (don't conflate them)

- **Content travels by file sync** (e.g. iCloud/Syncthing). The vault is the brain;
  its git history, if any, is local audit only. Edit a rule → sync carries it →
  agents inherit next run.
- **Tools travel by git.** The MCP server is code. New tools = `git pull` + restart.

Most changes are rule changes (channel 1). You touch git only when you add a tool.

## Coexistence primitives (what's in the policy)

- **Attribution, two forms.** Every section is attributable to its author: an agent
  signature in the heading (`## YYYY-MM-DD — <Agent>`) for direct writes, or a
  `<!-- src:<source>:<id> -->` marker for pipeline-ingested appends. No agent edits
  a section it doesn't own; conflicts are resolved by *appending*, never editing.
- **Guardrails.** Slugs matching sensitive domains (legal/financial/health) are
  propose-only — never auto-applied.
- **Durable vs ephemeral**, and for coding sources, a session **mode**
  (`discussion | execution | mixed`) so the digest extracts a decision from a grind
  but keeps a design discussion in full.
- **Sharded dedup ledger.** Each machine writes its own shard; readers union them.
  Removes the shared writable file instead of locking it.
- **REM / cross-links are propose-only.** No agent auto-mutates the graph's links.
- **Versioned.** `policy_version` recorded per run, so drift is visible.

## Degradation

- Policy unreadable → **fail closed** (a pass with no coexistence rules is the one
  that must not run).
- Policy stale → **proceed propose-only** (an old policy is still valid; just don't
  auto-write shared state under it).

## Adding a new agent

1. Register its identity + ingest boundary in the policy.
2. Have its pass call `get_consolidation_policy()` (or read the file directly if
   it's non-MCP) at run start; stamp `policy_version` in its journal.
3. Follow `get_workflow("dream-cycle")` for the pass shape; only its writer stage
   writes; recombination is propose-only.
4. Verify: bump `policy_version` in the vault → the agent's next run picks it up
   with no code change. That round-trip is the conformance proof.
