"""Graph-augmented re-ranking in search_hybrid (T3 backward-compat, T4 recall, T5 cache).

Semantic + grep are stubbed so these tests never load the embedding model; the
graph / PPR layer runs for real against the temp vault.
"""
import brain_mcp.vault as vault
import brain_mcp.vectors as vectors


def _stub_text(monkeypatch, sem_ids, grep_ids):
    """Make search_semantic / search_notes return fixed id lists, in order."""
    def fake_semantic(query, k=10, type_filter=None):
        return [
            {"id": nid, "type": "topic", "heading": None, "snippet": f"sem {nid}"}
            for nid in sem_ids
        ]

    def fake_grep(query, type_filter=None, updated_since=None, k=10):
        return [{"id": nid, "type": "topic", "snippet": f"grep {nid}"} for nid in grep_ids]

    monkeypatch.setattr(vectors, "search_semantic", fake_semantic)
    monkeypatch.setattr(vault, "search_notes", fake_grep)


# ---------- T3: backward compatibility (structural_weight=0) ----------


def test_weight_zero_is_pure_text_rrf(write_note, monkeypatch):
    for nid in ("a", "b", "c"):
        write_note("notes", nid, {"type": "topic"}, "body")
    _stub_text(monkeypatch, sem_ids=["a", "b", "c"], grep_ids=["b"])

    res = vectors.search_hybrid("q", k=10, structural_weight=0.0)
    ids = [r["id"] for r in res]

    # RRF: b appears in both lists → ranks first; a before c (semantic order).
    assert ids == ["b", "a", "c"]
    # No graph step ⇒ no connective notes injected, no graph-only provenance.
    assert all(r["via"] != ["graph"] for r in res)


def test_weight_zero_no_connective_injection(write_note, monkeypatch):
    for nid in ("a", "b"):
        write_note("notes", nid, {"type": "topic"}, "body")
    # connective links to both hits but is not itself a text hit
    write_note("notes", "bridge", {"type": "topic"}, "[[a]] [[b]]")
    _stub_text(monkeypatch, sem_ids=["a", "b"], grep_ids=[])

    res = vectors.search_hybrid("q", k=10, structural_weight=0.0)
    assert "bridge" not in {r["id"] for r in res}


# ---------- T4: connective-note recall (the payoff) ----------


def test_connective_note_surfaces_with_graph_weight(write_note, monkeypatch):
    # a, b, c are strong text hits; `bridge` matches no query text but links them.
    for nid in ("a", "b", "c"):
        write_note("notes", nid, {"type": "topic"}, "body")
    write_note("notes", "bridge", {"type": "topic"}, "[[a]] [[b]] [[c]]")
    _stub_text(monkeypatch, sem_ids=["a", "b", "c"], grep_ids=[])

    with_graph = vectors.search_hybrid("q", k=10, structural_weight=0.5)
    ids = {r["id"] for r in with_graph}
    assert "bridge" in ids
    bridge = next(r for r in with_graph if r["id"] == "bridge")
    assert bridge["via"] == ["graph"]

    # And it must be the GRAPH signal — not text — that surfaced it.
    without_graph = vectors.search_hybrid("q", k=10, structural_weight=0.0)
    assert "bridge" not in {r["id"] for r in without_graph}


def test_type_filter_applies_to_connective(write_note, monkeypatch):
    for nid in ("a", "b"):
        write_note("notes", nid, {"type": "topic"}, "body")
    write_note("notes", "bridge", {"type": "person"}, "[[a]] [[b]]")
    _stub_text(monkeypatch, sem_ids=["a", "b"], grep_ids=[])

    res = vectors.search_hybrid("q", k=10, type_filter="topic", structural_weight=0.5)
    assert "bridge" not in {r["id"] for r in res}


# ---------- T5: cache guard (no double parse) ----------


def test_non_positive_k_returns_empty(write_note, monkeypatch):
    # Guards the slice [: k * _CONNECTIVE_FACTOR] against sign inversion (DoS).
    write_note("notes", "a", {"type": "topic"}, "body")
    _stub_text(monkeypatch, sem_ids=["a"], grep_ids=[])
    assert vectors.search_hybrid("q", k=0, structural_weight=0.5) == []
    assert vectors.search_hybrid("q", k=-1, structural_weight=0.5) == []
    # search_graph wraps search_hybrid; the guard must hold there too.
    assert vectors.search_graph("q", k=0) == []
    assert vectors.search_graph("q", k=-1) == []


def test_search_hybrid_reuses_cached_graph(write_note, monkeypatch):
    for nid in ("a", "b"):
        write_note("notes", nid, {"type": "topic"}, "body")
    write_note("notes", "bridge", {"type": "topic"}, "[[a]] [[b]]")
    _stub_text(monkeypatch, sem_ids=["a", "b"], grep_ids=[])

    vault._graph_cached.cache_clear()
    vectors.search_hybrid("q", k=10, structural_weight=0.5)
    misses_after_first = vault._graph_cached.cache_info().misses
    vectors.search_hybrid("q", k=10, structural_weight=0.5)
    misses_after_second = vault._graph_cached.cache_info().misses

    # Vault unchanged between calls ⇒ graph rebuilt zero extra times.
    assert misses_after_second == misses_after_first
