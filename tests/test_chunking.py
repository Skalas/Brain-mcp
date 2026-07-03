"""Chunk-size ceiling and sub-splitting (T1, T2). Never loads the embedder."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

import brain_mcp.vectors as vectors
from brain_mcp.vault import Note

NEEDLE = "XENOPHANTIC_RETRIEVAL_TARGET"


def _note(body: str, note_id: str = "big") -> Note:
    return Note(
        id=note_id,
        path=Path("notes") / f"{note_id}.md",
        frontmatter={"title": note_id},
        body=body,
    )


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


def _needle_embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    """Mock embedder: needle-bearing passages align with query vectors."""
    del kind
    vecs: list[list[float]] = []
    for text in texts:
        vec = [0.0] * vectors.EMBED_DIM
        if NEEDLE in text:
            vec[0] = 1.0
            vec[1] = 0.5
        else:
            vec[0] = 0.0
            vec[1] = -1.0
        vecs.append(vec)
    return vecs


def _padding(n: int, char: str = "x") -> str:
    return char * n


# ---------- T1: chunk-size ceiling ----------


def test_small_note_single_chunk():
    note = _note("Short body with enough characters to pass the floor.")
    chunks = vectors.chunk_note(note)
    assert len(chunks) == 1
    assert chunks[0].section_idx == 0
    assert len(chunks[0].content) <= vectors.MAX_CHUNK_CHARS


def test_normal_h2_sections_stay_single_chunks():
    body = (
        "Intro line long enough to embed.\n\n"
        "## Alpha\n"
        "Alpha section content with sufficient length here.\n\n"
        "## Beta\n"
        "Beta section content with sufficient length here."
    )
    chunks = vectors.chunk_note(_note(body))
    assert [c.section_idx for c in chunks] == [0, 1, 2]
    assert all(len(c.content) <= vectors.MAX_CHUNK_CHARS for c in chunks)


def test_oversized_headingless_body_splits():
    body = _padding(3000) + f"\n\n{NEEDLE}"
    chunks = vectors.chunk_note(_note(body))
    assert len(chunks) > 1
    assert all(len(c.content) <= vectors.MAX_CHUNK_CHARS for c in chunks)
    assert any(NEEDLE in c.content for c in chunks)
    assert chunks[-1].section_idx >= vectors._SUB_IDX_SCALE


def test_oversized_h2_section_splits_with_stable_indices():
    body = (
        "## Huge\n"
        + _padding(3000, "a")
        + "\n\n### sub\n"
        + _padding(500, "b")
    )
    chunks = vectors.chunk_note(_note(body))
    section_chunks = [c for c in chunks if c.heading == "Huge"]
    assert len(section_chunks) > 1
    idxs = [c.section_idx for c in section_chunks]
    assert idxs == [(1 + 1) * vectors._SUB_IDX_SCALE + i for i in range(len(section_chunks))]
    assert all(len(c.content) <= vectors.MAX_CHUNK_CHARS for c in section_chunks)


def test_reindex_only_reembeds_changed_subchunks(
    write_note, clean_vector_store, monkeypatch, vault_root
):
    body = _padding(3000) + f"\n\n{NEEDLE}"
    write_note("notes", "big", {"title": "big", "type": "topic"}, body)
    monkeypatch.setattr(vectors, "_embed", _needle_embed)

    first = vectors.reindex_note("big")
    assert first["embedded"] >= 2

    note_path = vault_root / "notes" / "big.md"
    note_path.write_text(note_path.read_text(encoding="utf-8") + "!", encoding="utf-8")

    second = vectors.reindex_note("big")
    assert second["embedded"] == 1


# ---------- T2: content past ~2 KB is retrievable ----------


def test_needle_beyond_2kb_lands_in_subchunk():
    body = _padding(2500) + f"\n\n{NEEDLE}"
    chunks = vectors.chunk_note(_note(body))
    assert len(chunks) > 1
    assert any(NEEDLE in c.content for c in chunks)
    assert chunks[0].section_idx == 0 or chunks[0].section_idx >= vectors._SUB_IDX_SCALE


def test_semantic_search_finds_needle_past_2kb(
    write_note, clean_vector_store, monkeypatch
):
    body = _padding(2500) + f"\n\n{NEEDLE}"
    write_note("notes", "big", {"title": "big", "type": "topic"}, body)
    write_note("notes", "other", {"title": "other", "type": "topic"}, _padding(100))
    monkeypatch.setattr(vectors, "_embed", _needle_embed)

    vectors.reindex_note("big")
    vectors.reindex_note("other")

    hits = vectors.search_semantic(NEEDLE, k=5)
    ids = {h["id"] for h in hits}
    assert "big" in ids


# ---------- Blocker regressions ----------


def test_large_note_many_aliases_bounded_chunks():
    aliases = [f"alias-number-{i}-with-extra-length" for i in range(200)]
    body = _padding(300_000)
    note = Note(
        id="glossary",
        path=Path("notes") / "glossary.md",
        frontmatter={"title": "glossary", "aliases": aliases, "tags": ["ref"] * 50},
        body=body,
    )
    chunks = vectors.chunk_note(note)
    assert len(chunks) <= vectors._MAX_CHUNKS_PER_NOTE
    assert len(chunks) < 600
    assert all(len(c.content) <= vectors.MAX_CHUNK_CHARS for c in chunks)


def test_section_chunk_cap_logs_warning(caplog):
    note = _note("x" * 700_000, note_id="huge-section")
    with caplog.at_level(logging.WARNING, logger="brain_mcp.vectors"):
        chunks = vectors.chunk_note(note)
    assert len(chunks) == vectors._MAX_SUB_CHUNKS
    assert any(
        "sub-chunk" in r.message and "dropping" in r.message for r in caplog.records
    )


def test_note_aggregate_chunk_cap_logs_warning(caplog):
    section = "y" * 500_000
    body = "\n\n".join(f"## Section {i}\n{section}" for i in range(6))
    note = _note(body, note_id="multi-huge")
    with caplog.at_level(logging.WARNING, logger="brain_mcp.vectors"):
        chunks = vectors.chunk_note(note)
    assert len(chunks) == vectors._MAX_CHUNKS_PER_NOTE
    assert any("aggregate chunk cap" in r.message for r in caplog.records)


def test_section_indices_raises_too_many_parts():
    with pytest.raises(RuntimeError, match="note 'glossary'"):
        vectors._section_indices(0, vectors._SUB_IDX_SCALE, note_id="glossary")


def test_section_indices_raises_legacy_collision():
    with pytest.raises(RuntimeError, match="note 'big'"):
        vectors._section_indices(
            0, 2, note_id="big", max_legacy_single=vectors._SUB_IDX_SCALE
        )


def test_reindex_all_continues_on_failure(
    write_note, clean_vector_store, monkeypatch, vault_root
):
    import brain_mcp.vault as vault

    embed_fail = "BAD_EMBED_FAIL_TRIGGER"
    good_body = _padding(80) + " good-a stable content"
    bad_v1 = (
        "## Alpha\n"
        + _padding(80, "a")
        + "\n\n## Beta\n"
        + _padding(80, "b")
    )

    write_note("notes", "good-a", {"title": "good-a", "type": "topic"}, good_body)
    write_note("notes", "good-b", {"title": "good-b", "type": "topic"}, good_body.replace("good-a", "good-b"))
    write_note("notes", "bad", {"title": "bad", "type": "topic"}, bad_v1)
    write_note("notes", "orphan", {"title": "orphan", "type": "topic"}, _padding(80) + " orphan")

    monkeypatch.setattr(vectors, "_embed", _needle_embed)
    vectors.reindex_note("good-a")
    vectors.reindex_note("bad")
    vectors.reindex_note("orphan")

    conn = vectors._db()
    def _chunk_count(note_id: str) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE note_id = ?", (note_id,)
        ).fetchone()[0]

    good_a_before = _chunk_count("good-a")
    bad_before = _chunk_count("bad")
    assert good_a_before >= 1 and bad_before >= 2

    (vault_root / "notes" / "orphan.md").unlink()
    vault._graph_cached.cache_clear()

    bad_v2 = _padding(2500) + f"\n\n{embed_fail}\n\n## Only\n" + _padding(80, "z")
    (vault_root / "notes" / "bad.md").write_text(
        "---\n"
        + "title: bad\ntype: topic\n"
        + "---\n"
        + bad_v2,
        encoding="utf-8",
    )
    vault._graph_cached.cache_clear()

    def flaky_embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        if any(embed_fail in t for t in texts):
            raise RuntimeError("embed failed for bad note")
        return _needle_embed(texts, kind=kind)

    monkeypatch.setattr(vectors, "_embed", flaky_embed)

    totals = vectors.reindex_all(prune=True)

    assert totals["notes"] == 2
    assert len(totals["failed"]) == 1
    assert totals["failed"][0]["note_id"] == "bad"
    assert "embed failed" in totals["failed"][0]["error"]

    assert _chunk_count("good-a") == good_a_before
    assert _chunk_count("bad") == bad_before
    assert conn.execute(
        "SELECT 1 FROM chunks WHERE note_id = ?", ("orphan",)
    ).fetchall() == []
