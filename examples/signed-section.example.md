# What an append looks like (illustrative)

Two valid attribution forms under policy §1. These are how sections appear inside
an entity note — you don't create this file; it shows the convention.

## Direct write — agent signature

A conversational/orchestrator agent signs the heading with its identity:

```markdown
## 2026-01-15 — Nico
- Insight: consolidated the day's planning thread into the project note.
- Decision: deferred the migration to next sprint.
```

It may optionally add `<!-- policy:v1 -->` on the next line for greppable drift.

## Pipeline append — source marker

An ingestion pipeline (e.g. a coding agent digesting a session) uses a descriptive
heading and attributes via a source marker; no agent signature needed:

```markdown
## 2026-01-15 — Atomic writes with tempfile <!-- src:claude-jsonl:abc12345 -->
Use a temp file + os.replace for crash-safe writes. Links: [[python]].
```

## Rules either way

- An agent edits only sections it owns (its signature, or a source it owns).
- Conflicts are resolved by appending a new section — never by editing another's.
- Writes are append-only, which is what makes auto-merge of a sync conflict safe.
