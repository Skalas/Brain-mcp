"""Kind operations: list-state filter regression (#19) and add flow."""
import brain_mcp.kind_ops as ko
from brain_mcp.kinds import load_kinds


def _task():
    return load_kinds()["task"]


def test_list_items_accepts_list_state_filter(stub_reindex):
    kind = _task()
    # list-valued state must not raise TypeError (was `list in set`)
    result = ko.list_items(kind, where={"state": ["open", "done"]})
    assert isinstance(result, list)


def test_list_items_excludes_terminal_by_default(write_note):
    write_note("notes", "t-open", {"kind": "task", "state": "open", "title": "O"}, "x")
    write_note("notes", "t-done", {"kind": "task", "state": "done", "title": "D"}, "x")
    ids = {r["id"] for r in ko.list_items(_task(), where={})}
    assert "t-open" in ids and "t-done" not in ids


def test_list_items_terminal_when_explicitly_filtered(write_note):
    write_note("notes", "t-done2", {"kind": "task", "state": "done", "title": "D"}, "x")
    ids = {r["id"] for r in ko.list_items(_task(), where={"state": "done"})}
    assert "t-done2" in ids


def test_add_creates_note_and_state(stub_reindex):
    kind = _task()
    res = ko.add(kind, {"title": "Write tests"}, "body")
    note = ko.find_note_by_id(res["id"])
    assert note is not None
    assert note.frontmatter["kind"] == "task"
    assert note.frontmatter["state"] == "open"
    assert note.frontmatter["context"] == "personal"
