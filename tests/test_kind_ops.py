"""Kind operations: list-state filter regression (#19) and add flow."""
import brain_mcp.kind_ops as ko
from brain_mcp.kinds import load_kinds
from brain_mcp.vault import VAULT_PATH


def _task():
    return load_kinds()["task"]


def _book():
    return load_kinds()["book"]


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


def test_add_writes_to_kind_target_folder(stub_reindex):
    kind = _book()
    assert kind.folder == "library/books"
    res = ko.add(kind, {"title": "Dune"}, "sci-fi")
    path = VAULT_PATH / res["path"]
    assert path.parent == VAULT_PATH / "library" / "books"
    assert path.exists()


def test_find_sees_notes_in_shelf_folder_and_notes(write_note):
    kind = _book()
    write_note("library/books", "book-shelf", {"kind": "book", "title": "Shelf"}, "x")
    write_note("notes", "book-legacy", {"kind": "book", "title": "Legacy"}, "x")
    ids = {r["id"] for r in ko.find(kind)}
    assert "book-shelf" in ids
    assert "book-legacy" in ids
