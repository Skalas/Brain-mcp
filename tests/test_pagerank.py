"""Personalized PageRank over the wikilink graph (T1, T2, T5-perf)."""
import time

import brain_mcp.vault as vault


def _star(write_note, n):
    """Center 'hub' linking to leaf-0..leaf-(n-1)."""
    body = "".join(f"[[leaf-{i}]] " for i in range(n))
    write_note("notes", "hub", {"type": "topic"}, body)
    for i in range(n):
        write_note("notes", f"leaf-{i}", {"type": "topic"}, "leaf")


# ---------- T1: core correctness & edge cases ----------


def test_ppr_empty_seeds_returns_zeros(write_note):
    # No usable seed mass ⇒ every active note scores zero (no crash, no NaN).
    write_note("notes", "lonely", {"type": "topic"}, "body")
    pr = vault.personalized_pagerank({})
    assert pr and all(v == 0.0 for v in pr.values())


def test_ppr_single_node(write_note):
    write_note("notes", "solo", {"type": "topic"}, "alone")
    pr = vault.personalized_pagerank({"solo": 1.0})
    assert abs(pr["solo"] - 1.0) < 1e-9


def test_ppr_seed_absent_returns_zeros(write_note):
    write_note("notes", "a", {"type": "topic"}, "body")
    pr = vault.personalized_pagerank({"ghost-not-a-note": 1.0})
    assert "a" in pr and all(v == 0.0 for v in pr.values())


def test_ppr_sums_to_one(write_note):
    _star(write_note, 4)
    pr = vault.personalized_pagerank({"leaf-0": 1.0})
    assert abs(sum(pr.values()) - 1.0) < 1e-6


def test_ppr_deterministic(write_note):
    _star(write_note, 4)
    a = vault.personalized_pagerank({"leaf-0": 1.0})
    b = vault.personalized_pagerank({"leaf-0": 1.0})
    assert a == b


def test_ppr_disconnected_components(write_note):
    write_note("notes", "a1", {"type": "topic"}, "to [[a2]]")
    write_note("notes", "a2", {"type": "topic"}, "leaf")
    write_note("notes", "b1", {"type": "topic"}, "to [[b2]]")
    write_note("notes", "b2", {"type": "topic"}, "leaf")
    pr = vault.personalized_pagerank({"a1": 1.0})
    # Mass stays in the seed's component; the other island gets nothing.
    assert pr["a1"] > 0 and pr["a2"] > 0
    assert pr["b1"] == 0.0 and pr["b2"] == 0.0


# ---------- T2: query-seeded expansion (proximity) ----------


def test_ppr_neighbor_beats_distant(write_note):
    # chain: seed -> mid -> far
    write_note("notes", "seed", {"type": "topic"}, "to [[mid]]")
    write_note("notes", "mid", {"type": "topic"}, "to [[far]]")
    write_note("notes", "far", {"type": "topic"}, "leaf")
    pr = vault.personalized_pagerank({"seed": 1.0})
    # Expansion/proximity: a 1-hop neighbor of the seed outranks a 2-hop note,
    # and the seed itself retains more mass than the distant note.
    assert pr["mid"] > pr["far"] > 0.0
    assert pr["seed"] > pr["far"]


def test_ppr_weights_bias_toward_heavier_seed(write_note):
    write_note("notes", "x", {"type": "topic"}, "leaf")
    write_note("notes", "y", {"type": "topic"}, "leaf")
    pr = vault.personalized_pagerank({"x": 3.0, "y": 1.0})
    assert pr["x"] > pr["y"]


# ---------- T5: performance guard ----------


def test_ppr_perf_budget(write_note):
    # A few hundred nodes / edges — well above current vault scale.
    n = 250
    body = "".join(f"[[leaf-{i}]] " for i in range(n))
    write_note("notes", "hub", {"type": "topic"}, body)
    for i in range(n):
        nxt = f"[[leaf-{(i + 1) % n}]]"
        write_note("notes", f"leaf-{i}", {"type": "topic"}, f"ring {nxt}")
    t0 = time.perf_counter()
    pr = vault.personalized_pagerank({"hub": 1.0})
    elapsed = time.perf_counter() - t0
    assert pr  # non-empty
    # Generous ceiling: flags algorithmic regressions, not micro-noise.
    assert elapsed < 2.0, f"PPR took {elapsed:.2f}s over {n + 1} nodes"
