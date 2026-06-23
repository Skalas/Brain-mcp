"""Path-traversal and read-only guards (#15, #16, #17)."""
from pathlib import Path

import pytest

import brain_mcp.vault as vault
from brain_mcp.vault import VaultError


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "..", "a/b", "/etc/hosts", ".obsidian/x", "../_system/CLAUDE",
])
def test_find_note_by_id_rejects_traversal(bad):
    assert vault.find_note_by_id(bad) is None


def test_find_note_by_id_resolves_legit(write_note):
    write_note("notes", "real-note", {"type": "topic"}, "hi")
    assert vault.find_note_by_id("real-note") is not None


@pytest.mark.parametrize("bad", ["../CLAUDE", "../../../../etc/hosts", "a/b", "..", "foo/../bar"])
def test_get_workflow_rejects_traversal(bad):
    with pytest.raises(VaultError):
        vault.get_workflow(bad)


def test_get_workflow_reads_real(vault_root):
    body = vault.get_workflow("sample-workflow")
    assert "Sample Workflow" in body


def test_assert_inside_vault_blocks_outside_and_readonly(vault_root):
    with pytest.raises(VaultError):
        vault.assert_inside_vault(Path("/etc/passwd"))
    with pytest.raises(VaultError):
        vault.assert_inside_vault(vault_root / "_system" / "CLAUDE.md")


def test_error_messages_have_no_absolute_paths(vault_root):
    for call in (
        lambda: vault.assert_inside_vault(Path("/etc/passwd")),
        lambda: vault.read_index("../../secret"),
        lambda: vault.read_doctrine() if False else vault.get_workflow("../x"),
    ):
        try:
            call()
        except VaultError as e:
            assert str(vault_root) not in str(e)
            assert "/Users/" not in str(e)


@pytest.mark.parametrize("bad", [
    "../../../../etc/hosts", "../_system/CLAUDE", "..", "a/b", "/etc/passwd",
])
def test_restore_note_rejects_traversal(bad, stub_reindex):
    import brain_mcp.writes as writes
    with pytest.raises(VaultError):
        writes.restore_note(bad)


def test_append_line_blocks_readonly(vault_root):
    import brain_mcp.kind_ops as ko
    from brain_mcp.kinds import load_kinds
    kind = next(iter(load_kinds().values()))
    with pytest.raises(VaultError):
        ko._append_line(vault_root / "_system" / "CLAUDE.md", "- pwned", kind)
