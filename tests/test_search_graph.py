"""search_graph neighbor determinism and k contract (T5, T6)."""
import brain_mcp.vault as vault
import brain_mcp.vectors as vectors


def _stub_hybrid(monkeypatch, seed_scores: dict[str, float]):
    def fake_hybrid(query, k=10, type_filter=None, structural_weight=0.3):
        del query, type_filter, structural_weight
        ranked = sorted(seed_scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [
            {"id": nid, "type": "topic", "heading": None, "snippet": nid, "score": score, "via": ["semantic"]}
            for nid, score in ranked
        ]

    monkeypatch.setattr(vectors, "search_hybrid", fake_hybrid)


def test_search_graph_neighbors_ranked_by_degree(write_note, monkeypatch):
    # seed links directly to leaves; pick top-2 by graph degree.
    write_note(
        "notes",
        "seed",
        {"type": "topic"},
        "[[leaf-high]] [[leaf-mid]] [[leaf-low]]",
    )
    write_note("notes", "leaf-high", {"type": "topic"}, "[[extra-1]] [[extra-2]]")
    write_note("notes", "leaf-mid", {"type": "topic"}, "[[extra-1]]")
    write_note("notes", "leaf-low", {"type": "topic"}, "leaf")
    write_note("notes", "extra-1", {"type": "topic"}, "x")
    write_note("notes", "extra-2", {"type": "topic"}, "y")

    _stub_hybrid(monkeypatch, {"seed": 1.0})
    res = vectors.search_graph("q", k=10, neighbors_per_seed=2)

    graph_neighbors = [r for r in res if r.get("source") == "graph"]
    assert [r["id"] for r in graph_neighbors] == ["leaf-high", "leaf-mid"]


def test_search_graph_neighbor_order_stable(write_note, monkeypatch):
    write_note(
        "notes",
        "seed",
        {"type": "topic"},
        "[[leaf-high]] [[leaf-mid]] [[leaf-low]]",
    )
    write_note("notes", "leaf-high", {"type": "topic"}, "[[extra-1]] [[extra-2]]")
    write_note("notes", "leaf-mid", {"type": "topic"}, "[[extra-1]]")
    write_note("notes", "leaf-low", {"type": "topic"}, "leaf")
    write_note("notes", "extra-1", {"type": "topic"}, "x")
    write_note("notes", "extra-2", {"type": "topic"}, "y")

    _stub_hybrid(monkeypatch, {"seed": 1.0})

    vault._graph_cached.cache_clear()
    first = [r["id"] for r in vectors.search_graph("q", k=10, neighbors_per_seed=3) if r.get("source") == "graph"]
    vault._graph_cached.cache_clear()
    second = [r["id"] for r in vectors.search_graph("q", k=10, neighbors_per_seed=3) if r.get("source") == "graph"]

    assert first == second == ["leaf-high", "leaf-mid", "leaf-low"]


def test_search_graph_respects_k_total(write_note, monkeypatch):
    for i in range(5):
        write_note("notes", f"seed-{i}", {"type": "topic"}, f"[[n-{i}-a]] [[n-{i}-b]]")
        write_note("notes", f"n-{i}-a", {"type": "topic"}, "a")
        write_note("notes", f"n-{i}-b", {"type": "topic"}, "b")

    _stub_hybrid(monkeypatch, {f"seed-{i}": 1.0 - i * 0.1 for i in range(5)})

    res = vectors.search_graph("q", k=4, neighbors_per_seed=2)
    assert len(res) <= 4


def test_search_graph_never_evicts_seed_for_neighbor(write_note, monkeypatch):
    write_note("notes", "weak-seed", {"type": "topic"}, "[[hub]] weak")
    write_note("notes", "strong-seed", {"type": "topic"}, "[[hub]] strong")
    write_note("notes", "hub", {"type": "topic"}, "connector")

    _stub_hybrid(monkeypatch, {"weak-seed": 0.1, "strong-seed": 0.9})

    res = vectors.search_graph("q", k=2, neighbors_per_seed=5)
    ids = {r["id"] for r in res}

    assert len(res) <= 2
    assert ids == {"weak-seed", "strong-seed"}
    assert "hub" not in ids


def test_search_graph_result_sorted_by_score_descending(write_note, monkeypatch):
    write_note("notes", "weak-seed", {"type": "topic"}, "[[hub]] weak")
    write_note("notes", "strong-seed", {"type": "topic"}, "[[hub]] strong")
    write_note("notes", "hub", {"type": "topic"}, "connector")

    _stub_hybrid(monkeypatch, {"weak-seed": 0.1, "strong-seed": 0.9})

    res = vectors.search_graph("q", k=3, neighbors_per_seed=5)
    scores = [r["score"] for r in res]
    ids = [r["id"] for r in res]

    assert scores == sorted(scores, reverse=True)
    assert "weak-seed" in ids
    assert "hub" in ids
    assert ids.index("hub") < ids.index("weak-seed")
