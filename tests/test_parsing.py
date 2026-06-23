"""Frontmatter parsing edge cases (#18 CRLF, #26 YAML errors)."""
import logging

import brain_mcp.vault as vault


def test_parse_note_crlf(write_note):
    path = write_note("notes", "crlf", {"type": "project", "title": "Hi"}, "body")
    # rewrite with CRLF line endings to simulate a Windows/iCloud roundtrip
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    note = vault.parse_note(path)
    assert note.frontmatter.get("type") == "project"
    assert note.frontmatter.get("title") == "Hi"
    assert "\r" not in note.body


def test_parse_note_malformed_yaml_logs_and_flags(write_note, caplog):
    path = write_note("notes", "broken", body="x")
    path.write_text("---\nkey: {broken yaml\n---\nbody\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="brain_mcp.vault"):
        note = vault.parse_note(path)
    assert note.frontmatter.get("_parse_error") is True
    assert any("Malformed YAML" in r.getMessage() for r in caplog.records)


def test_parse_note_no_frontmatter(write_note):
    path = write_note("notes", "plain")
    path.write_text("just a body, no frontmatter\n", encoding="utf-8")
    note = vault.parse_note(path)
    assert note.frontmatter == {}
    assert "just a body" in note.body
