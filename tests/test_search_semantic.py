"""search_semantic dedup / over-fetch correctness (T4). Never loads the embedder."""
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


def _ranked_embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    """Each note id encodes a distinct vector; shared marker boosts similarity."""
    del kind
    vecs: list[list[float]] = []
    for text in texts:
        vec = [0.0] * vectors.EMBED_DIM
        if "note-dom" in text:
            vec[0] = 1.0
        elif "note-" in text:
            # extract note-N token from header blob
            for token in text.split():
                if token.startswith("note-") and token[5:].isdigit():
                    slot = int(token[5:])
                    vec[slot] = 1.0
                    break
        else:
            vec[2] = 0.1
        vecs.append(vec)
    return vecs


def _query_embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    del kind
    return [[1.0] + [0.0] * (vectors.EMBED_DIM - 1) for _ in texts]


def test_multi_section_note_still_returns_k_distinct_notes(
    write_note, clean_vector_store, monkeypatch
):
    # One note with many H2 sections sharing the same marker (dominates KNN rows).
    sections = "\n\n".join(
        f"## Section {i}\nnote-dom shared marker section {i} with enough text."
        for i in range(8)
    )
    write_note("notes", "note-dom", {"title": "note-dom", "type": "topic"}, sections)
    for i in range(6):
        write_note(
            "notes",
            f"note-{i + 1}",
            {"title": f"note-{i + 1}", "type": "topic"},
            f"note-{i + 1} unrelated filler content number {i + 1}.",
        )

    monkeypatch.setattr(vectors, "_embed", lambda texts, kind="passage": (
        _query_embed(texts, kind=kind) if kind == "query" else _ranked_embed(texts, kind=kind)
    ))

    for nid in ["note-dom"] + [f"note-{i + 1}" for i in range(6)]:
        vectors.reindex_note(nid)

    hits = vectors.search_semantic("note-dom marker", k=5)
    assert len(hits) == 5
    assert len({h["id"] for h in hits}) == 5


def test_type_filter_still_caps_at_k(write_note, clean_vector_store, monkeypatch):
    for i in range(8):
        write_note(
            "notes",
            f"t-{i}",
            {"title": f"t-{i}", "type": "topic" if i % 2 == 0 else "person"},
            f"t-{i} note-dom marker content here.",
        )

    monkeypatch.setattr(vectors, "_embed", lambda texts, kind="passage": (
        _query_embed(texts, kind=kind) if kind == "query" else _ranked_embed(texts, kind=kind)
    ))

    for i in range(8):
        vectors.reindex_note(f"t-{i}")

    hits = vectors.search_semantic("note-dom", k=5, type_filter="topic")
    assert len(hits) <= 5
    assert all(h["type"] == "topic" for h in hits)
