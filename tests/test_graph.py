"""Connection queries and centrality (#9, #10)."""
import brain_mcp.vault as vault


def _star(write_note, n):
    """Center 'hub' linking to leaf-0..leaf-(n-1)."""
    body = "".join(f"[[leaf-{i}]] " for i in range(n))
    write_note("notes", "hub", {"type": "topic"}, body)
    for i in range(n):
        write_note("notes", f"leaf-{i}", {"type": "topic"}, "leaf")


def test_neighborhood_depth1(write_note):
    _star(write_note, 3)
    nb = vault.neighborhood("hub", 1)
    assert nb["root"] == "hub" and nb["depth"] == 1
    assert nb["nodes"][0] == {"id": "hub", "distance": 0, "type": "topic", "title": "hub"}
    d1 = {n["id"] for n in nb["nodes"] if n["distance"] == 1}
    assert d1 == {"leaf-0", "leaf-1", "leaf-2"}
    assert not nb["truncated"]


def test_neighborhood_depth_clamped(write_note):
    _star(write_note, 2)
    assert vault.neighborhood("hub", 99)["depth"] == 3
    assert vault.neighborhood("hub", 0)["depth"] == 1


def test_neighborhood_truncation(write_note):
    _star(write_note, vault.MAX_NEIGHBORHOOD_NODES + 5)
    nb = vault.neighborhood("hub", 1)
    assert nb["truncated"] is True
    assert len(nb["nodes"]) == vault.MAX_NEIGHBORHOOD_NODES


def test_path_between_self_and_symmetric(write_note):
    write_note("notes", "p", {"type": "topic"}, "to [[q]]")
    write_note("notes", "q", {"type": "topic"}, "to [[r]]")
    write_note("notes", "r", {"type": "topic"}, "leaf")
    assert vault.path_between("p", "p")["length"] == 0
    fwd = vault.path_between("p", "r")
    assert fwd["connected"] and fwd["path"] == ["p", "q", "r"] and fwd["length"] == 2
    assert vault.path_between("r", "p")["length"] == 2  # undirected


def test_path_between_disconnected(write_note):
    write_note("notes", "island-a", {"type": "topic"}, "alone")
    write_note("notes", "island-b", {"type": "topic"}, "also alone")
    res = vault.path_between("island-a", "island-b")
    assert res == {"connected": False, "length": None, "path": [], "nodes": []}


def test_centrality_normalized(write_note):
    _star(write_note, 4)
    cen = vault.centrality()
    assert abs(cen["hub"] - 1.0) < 1e-9
    assert all(0.0 <= v <= 1.0 for v in cen.values())
    assert cen["leaf-0"] < cen["hub"]
