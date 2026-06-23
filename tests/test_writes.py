"""Write operations: create, edit, archive ordering (#20), reindex fallback (#23)."""
import pytest

import brain_mcp.vault as vault
import brain_mcp.writes as writes
from brain_mcp.vault import VaultError


def test_create_note_defaults_work_context(stub_reindex):
    res = writes.create_note("topic", "ctx-note", {}, "body")
    note = vault.find_note_by_id("ctx-note")
    assert note.frontmatter["context"] == "work"
    assert note.frontmatter["type"] == "topic"


def test_create_note_rejects_bad_type(stub_reindex):
    with pytest.raises(VaultError):
        writes.create_note("bogus", "x", {}, "b")


def test_edit_note_unique_match_required(stub_reindex):
    writes.create_note("topic", "edit-me", {}, "alpha beta alpha")
    with pytest.raises(VaultError):  # appears twice
        writes.edit_note("edit-me", "alpha", "ZZZ")
    res = writes.edit_note("edit-me", "beta", "BETA")
    assert vault.find_note_by_id("edit-me").body.count("BETA") == 1


def test_archive_creates_dir_before_stripping_refs(stub_reindex, monkeypatch):
    import pathlib
    writes.create_note("topic", "victim", {}, "content")
    writes.create_note("topic", "referrer", {}, "see [[victim]]")

    real_mkdir = pathlib.Path.mkdir

    def fake_mkdir(self, *a, **k):
        if self == vault.ARCHIVE_DIR:
            raise OSError("boom")
        return real_mkdir(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "mkdir", fake_mkdir)
    with pytest.raises(OSError):
        writes.archive_note("victim", strip_refs=True)
    # referrer must NOT have been rewritten, because mkdir runs before strip
    assert "[[victim]]" in vault.find_note_by_id("referrer").body


def test_run_reindex_fallback_without_script(monkeypatch):
    monkeypatch.setattr(writes, "REINDEX_SCRIPT", writes.REINDEX_SCRIPT.parent / "missing.sh")
    # no note_id -> skip path, never touches the embedder
    msg = writes.run_reindex(None)
    assert "skipped" in msg and "script" in msg


def test_run_reindex_fallback_with_note_id(monkeypatch):
    monkeypatch.setattr(writes, "REINDEX_SCRIPT", writes.REINDEX_SCRIPT.parent / "missing.sh")
    import brain_mcp.vectors as vectors
    monkeypatch.setattr(vectors, "reindex_note", lambda nid: {"note_id": nid})
    msg = writes.run_reindex("some-id")
    assert "vectors only" in msg
