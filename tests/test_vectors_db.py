"""Vector DB connection setup (#27). Touches sqlite only — never loads the embedder."""
import brain_mcp.vault as vault
import brain_mcp.kind_ops as ko
import brain_mcp.vectors as vectors


def test_db_uses_wal():
    conn = vectors._db()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_context_defaults_are_distinct():
    # guards against a future refactor silently unifying the two defaults (#24)
    assert vault.DEFAULT_CONTEXT == "work"
    assert ko.DEFAULT_CONTEXT == "personal"
    assert vault.DEFAULT_CONTEXT != ko.DEFAULT_CONTEXT
