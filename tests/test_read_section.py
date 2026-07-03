"""read_section: section-level retrieval + locator round-trip (Part A). Never loads the embedder."""
import pytest

import brain_mcp.vectors as vectors


@pytest.fixture
def clean_vector_store():
    vectors._db.cache_clear()
    conn = vectors._db()
    conn.execute("DELETE FROM vec_chunks")
    conn.execute("DELETE FROM chunks")
    conn.commit()
    yield
    conn.execute("DELETE FROM vec_chunks")
    conn.execute("DELETE FROM chunks")
    conn.commit()
    vectors._db.cache_clear()


def _marker_embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    """Deterministic vectors: the 'target' marker aligns with the query direction."""
    del kind
    vecs: list[list[float]] = []
    for text in texts:
        vec = [0.0] * vectors.EMBED_DIM
        vec[0] = 1.0 if "target-marker" in text else 0.05
        vecs.append(vec)
    return vecs


def _query_embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    del kind
    return [[1.0] + [0.0] * (vectors.EMBED_DIM - 1) for _ in texts]


def _install_stub_embed(monkeypatch):
    monkeypatch.setattr(vectors, "_embed", lambda texts, kind="passage": (
        _query_embed(texts, kind=kind) if kind == "query" else _marker_embed(texts, kind=kind)
    ))


# ---------- T1 / T3: search -> read_section round-trip ----------

def test_read_section_returns_full_body_not_snippet(write_note, clean_vector_store, monkeypatch):
    body = "## Deep Section\ntarget-marker " + ("lorem ipsum dolor sit amet " * 20)
    write_note("notes", "deep-note", {"title": "deep-note", "type": "topic"}, body)
    _install_stub_embed(monkeypatch)
    vectors.reindex_note("deep-note")

    hits = vectors.search_semantic("target-marker", k=5)
    top = next(h for h in hits if h["id"] == "deep-note")

    section = vectors.read_section(top["id"], top["section_idx"])

    # The read carries the full section body — longer than the 240-char snippet.
    assert section["id"] == "deep-note"
    assert section["section_idx"] == top["section_idx"]
    assert len(section["content"]) > len(top["snippet"])
    # And the snippet the search returned is derived from exactly this section.
    assert vectors._snippet(section["content"]) == top["snippet"]


def test_hybrid_locator_round_trips_into_read_section(write_note, clean_vector_store, monkeypatch):
    write_note(
        "notes", "hy-note", {"title": "hy-note", "type": "topic"},
        "## Body\ntarget-marker content that is comfortably long enough to index and read back.",
    )
    _install_stub_embed(monkeypatch)
    vectors.reindex_note("hy-note")

    hits = vectors.search_hybrid("target-marker", k=5, structural_weight=0)
    hit = next(h for h in hits if h["id"] == "hy-note")

    # Semantic-backed hybrid hits carry a usable locator...
    assert hit["section_idx"] is not None
    section = vectors.read_section(hit["id"], hit["section_idx"])
    assert "target-marker" in section["content"]


def test_hybrid_payload_always_carries_section_idx_key(write_note, clean_vector_store, monkeypatch):
    write_note(
        "notes", "k-note", {"title": "k-note", "type": "topic"},
        "## Body\ntarget-marker text long enough to survive the min-chunk floor easily.",
    )
    _install_stub_embed(monkeypatch)
    vectors.reindex_note("k-note")

    hits = vectors.search_hybrid("target-marker", k=5, structural_weight=0)
    # Every hit exposes the key (None for non-semantic provenance), so callers can
    # branch on it without KeyErrors.
    assert all("section_idx" in h for h in hits)


# ---------- T4: sub-chunk locators (last sprint's split keys) resolve ----------

def test_subchunk_section_idx_resolves(write_note, clean_vector_store, monkeypatch):
    # A single oversized section sub-splits into (base+1)*_SUB_IDX_SCALE + i keys.
    big = "## Big\ntarget-marker " + ("palabra " * 1200)  # well over MAX_CHUNK_CHARS
    write_note("notes", "big-note", {"title": "big-note", "type": "topic"}, big)
    _install_stub_embed(monkeypatch)
    vectors.reindex_note("big-note")

    conn = vectors._db()
    idxs = [
        r[0] for r in conn.execute(
            "SELECT section_idx FROM chunks WHERE note_id = ? ORDER BY section_idx",
            ("big-note",),
        ).fetchall()
    ]
    # Proof the note actually sub-split into stable sub-indices, not one chunk.
    assert any(idx >= vectors._SUB_IDX_SCALE for idx in idxs)

    for idx in idxs:
        section = vectors.read_section("big-note", idx)
        assert section["section_idx"] == idx
        assert section["content"]


# ---------- T5: unknown locators raise a clean domain error ----------

def test_read_section_unknown_note_raises(clean_vector_store):
    with pytest.raises(ValueError, match="No indexed section"):
        vectors.read_section("does-not-exist", 0)


def test_read_section_unknown_section_raises(write_note, clean_vector_store, monkeypatch):
    write_note(
        "notes", "small-note", {"title": "small-note", "type": "topic"},
        "## Only\ntarget-marker just one indexed section here, nothing more.",
    )
    _install_stub_embed(monkeypatch)
    vectors.reindex_note("small-note")

    with pytest.raises(ValueError, match="No indexed section"):
        vectors.read_section("small-note", 999999)
