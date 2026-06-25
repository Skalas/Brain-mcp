# Examples — DreamCore coexistence pattern

Paste-ready templates for the multi-agent coexistence pattern described in
[`../docs/dreamcore.md`](../docs/dreamcore.md). Copy them into your vault under
`_system/` and adapt; they're sanitized — no real notes, names tweak freely.

| File | Drop it at | Served by |
|---|---|---|
| [`dream-policy.example.md`](dream-policy.example.md) | `_system/dream-policy.md` | `get_consolidation_policy()` |
| [`dream-cycle.example.md`](dream-cycle.example.md) | `_system/recipes/dream-cycle.md` | `get_workflow("dream-cycle")` |
| [`signed-section.example.md`](signed-section.example.md) | (illustrative — what an append looks like) | — |

After dropping the files in, restart the MCP server so `get_workflow` lists the
new recipe. Then point each agent's dream pass at `get_consolidation_policy()`.
