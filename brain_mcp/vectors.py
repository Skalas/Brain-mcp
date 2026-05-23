"""Local vector search over the vault using sqlite-vec + fastembed (multilingual-e5-large)."""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .vault import (
    Note,
    find_note_by_id,
    iter_notes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
EMBED_MODEL = os.environ.get("BRAIN_EMBED_MODEL", "intfloat/multilingual-e5-large")
EMBED_DIM = int(os.environ.get("BRAIN_EMBED_DIM", "1024"))
# e5 models expect explicit "query:" / "passage:" prefixes; auto-applied when model name starts with "intfloat/".
_E5_FAMILY = EMBED_MODEL.startswith("intfloat/")
DB_PATH = Path(os.environ.get("BRAIN_VECTOR_DB", str(REPO_ROOT / ".vectors.db")))
MIN_CHUNK_CHARS = 40

H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


@dataclass
class Chunk:
    note_id: str
    section_idx: int
    heading: str
    content: str

    @property
    def hash(self) -> str:
        return hashlib.sha1(self.content.encode("utf-8")).hexdigest()


# ---------- db ----------


@lru_cache(maxsize=1)
def _db() -> sqlite3.Connection:
    import sqlite_vec

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chunks (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            note_id      TEXT NOT NULL,
            section_idx  INTEGER NOT NULL,
            heading      TEXT NOT NULL,
            content      TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            UNIQUE(note_id, section_idx)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS chunks_note_id_idx ON chunks(note_id)")
    conn.execute(
        f"CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks "
        f"USING vec0(embedding float[{EMBED_DIM}] distance_metric=cosine)"
    )
    conn.commit()
    return conn


# ---------- embedder ----------


@lru_cache(maxsize=1)
def _embedder():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=EMBED_MODEL)


def _embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    if _E5_FAMILY:
        prefix = "query: " if kind == "query" else "passage: "
        texts = [prefix + t for t in texts]
    return [list(v) for v in _embedder().embed(texts)]


def _to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------- chunking ----------


def chunk_note(note: Note) -> list[Chunk]:
    """Split a note into chunks. First chunk = preamble (frontmatter summary + lead text)
    up to first H2; following chunks = each H2 section."""
    title = str(note.frontmatter.get("title") or note.id)
    aliases = note.frontmatter.get("aliases") or []
    tags = note.frontmatter.get("tags") or []
    header_blob = " | ".join(
        [f"title: {title}"]
        + ([f"aliases: {', '.join(map(str, aliases))}"] if aliases else [])
        + ([f"tags: {', '.join(map(str, tags))}"] if tags else [])
    )

    body = note.body.strip()
    matches = list(H2_RE.finditer(body))

    chunks: list[Chunk] = []

    if not matches:
        text = f"{header_blob}\n\n{body}".strip()
        if len(text) >= MIN_CHUNK_CHARS:
            chunks.append(Chunk(note.id, 0, title, text))
        return chunks

    preamble = body[: matches[0].start()].strip()
    pre_text = f"{header_blob}\n\n{preamble}".strip()
    if len(pre_text) >= MIN_CHUNK_CHARS:
        chunks.append(Chunk(note.id, 0, title, pre_text))

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_text = body[m.start() : end].strip()
        if len(section_text) < MIN_CHUNK_CHARS:
            continue
        heading = m.group(1).strip()
        chunks.append(
            Chunk(
                note_id=note.id,
                section_idx=i + 1,
                heading=heading,
                content=f"{header_blob}\n\n{section_text}",
            )
        )
    return chunks


# ---------- indexing ----------


def reindex_note(note_id: str) -> dict:
    """Re-embed only sections whose content hash changed. Removes stale sections."""
    note = find_note_by_id(note_id)
    if note is None:
        return _delete_note(note_id, reason="missing")

    conn = _db()
    new_chunks = chunk_note(note)
    new_by_idx = {c.section_idx: c for c in new_chunks}

    existing: dict[int, tuple[int, str]] = {}
    for row in conn.execute(
        "SELECT id, section_idx, content_hash FROM chunks WHERE note_id = ?",
        (note_id,),
    ).fetchall():
        existing[row[1]] = (row[0], row[2])

    to_delete_ids = [
        rec[0] for idx, rec in existing.items() if idx not in new_by_idx
    ]
    to_upsert: list[Chunk] = [
        c for c in new_chunks
        if c.section_idx not in existing or existing[c.section_idx][1] != c.hash
    ]

    for chunk_id in to_delete_ids:
        conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (chunk_id,))
        conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))

    if to_upsert:
        embeddings = _embed([c.content for c in to_upsert])
        for chunk, vec in zip(to_upsert, embeddings):
            old = existing.get(chunk.section_idx)
            if old is not None:
                chunk_id = old[0]
                conn.execute(
                    "UPDATE chunks SET heading=?, content=?, content_hash=? WHERE id=?",
                    (chunk.heading, chunk.content, chunk.hash, chunk_id),
                )
                conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (chunk_id,))
            else:
                cur = conn.execute(
                    "INSERT INTO chunks(note_id, section_idx, heading, content, content_hash) VALUES (?,?,?,?,?)",
                    (chunk.note_id, chunk.section_idx, chunk.heading, chunk.content, chunk.hash),
                )
                chunk_id = cur.lastrowid
            conn.execute(
                "INSERT INTO vec_chunks(rowid, embedding) VALUES (?, ?)",
                (chunk_id, _to_blob(vec)),
            )

    conn.commit()
    return {
        "note_id": note_id,
        "embedded": len(to_upsert),
        "deleted": len(to_delete_ids),
        "total_chunks": len(new_chunks),
    }


def _delete_note(note_id: str, reason: str = "deleted") -> dict:
    conn = _db()
    rows = conn.execute("SELECT id FROM chunks WHERE note_id = ?", (note_id,)).fetchall()
    for (chunk_id,) in rows:
        conn.execute("DELETE FROM vec_chunks WHERE rowid = ?", (chunk_id,))
        conn.execute("DELETE FROM chunks WHERE id = ?", (chunk_id,))
    conn.commit()
    return {"note_id": note_id, "deleted": len(rows), "reason": reason}


def rebuild_all() -> dict:
    """Drop the vector store and re-embed every note from scratch.

    Use this when the embedding model's pooling strategy or dimensionality
    changes, so the corpus doesn't end up mixing incompatible vectors.
    """
    conn = _db()
    conn.execute("DROP TABLE IF EXISTS vec_chunks")
    conn.execute("DELETE FROM chunks")
    conn.execute(
        f"CREATE VIRTUAL TABLE vec_chunks "
        f"USING vec0(embedding float[{EMBED_DIM}] distance_metric=cosine)"
    )
    conn.commit()
    return reindex_all(prune=False)


def reindex_all(prune: bool = True) -> dict:
    """Walk the vault and reindex every note. If prune, drop chunks for notes that no longer exist."""
    conn = _db()
    live_ids: set[str] = set()
    totals = {"notes": 0, "embedded": 0, "deleted": 0, "chunks": 0}

    for note in iter_notes():
        live_ids.add(note.id)
        result = reindex_note(note.id)
        totals["notes"] += 1
        totals["embedded"] += result["embedded"]
        totals["deleted"] += result["deleted"]
        totals["chunks"] += result["total_chunks"]

    if prune:
        stale = {
            row[0]
            for row in conn.execute("SELECT DISTINCT note_id FROM chunks").fetchall()
            if row[0] not in live_ids
        }
        for note_id in stale:
            r = _delete_note(note_id, reason="pruned")
            totals["deleted"] += r["deleted"]

    return totals


# ---------- search ----------


def search_semantic(
    query: str,
    k: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    conn = _db()
    qvec = _embed([query], kind="query")[0]
    # Over-fetch when filtering, since type filter is applied after KNN.
    fetch = k * 4 if type_filter else k
    rows = conn.execute(
        """
        SELECT c.note_id, c.section_idx, c.heading, c.content, v.distance
        FROM vec_chunks v
        JOIN chunks c ON c.id = v.rowid
        WHERE v.embedding MATCH ? AND k = ?
        ORDER BY v.distance
        """,
        (_to_blob(qvec), fetch),
    ).fetchall()

    out: list[dict] = []
    seen: set[str] = set()
    for note_id, section_idx, heading, content, distance in rows:
        if note_id in seen:
            continue
        note = find_note_by_id(note_id)
        note_type = note.frontmatter.get("type") if note else None
        if type_filter and note_type != type_filter:
            continue
        seen.add(note_id)
        out.append(
            {
                "id": note_id,
                "section_idx": section_idx,
                "heading": heading,
                "type": note_type,
                "score": round(1.0 - float(distance), 4),
                "snippet": _snippet(content),
            }
        )
        if len(out) >= k:
            break
    return out


def search_hybrid(
    query: str,
    k: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    """Reciprocal-rank-fusion of semantic + grep results."""
    from . import vault

    sem = search_semantic(query, k=k, type_filter=type_filter)
    grep = vault.search_notes(query, type_filter, None, k)

    rrf_k = 60
    scores: dict[str, float] = {}
    payload: dict[str, dict] = {}

    for rank, hit in enumerate(sem):
        nid = hit["id"]
        scores[nid] = scores.get(nid, 0.0) + 1.0 / (rrf_k + rank + 1)
        payload[nid] = {
            "id": nid,
            "type": hit.get("type"),
            "heading": hit.get("heading"),
            "snippet": hit["snippet"],
            "via": ["semantic"],
        }
    for rank, hit in enumerate(grep):
        nid = hit["id"]
        scores[nid] = scores.get(nid, 0.0) + 1.0 / (rrf_k + rank + 1)
        if nid in payload:
            payload[nid]["via"].append("grep")
        else:
            payload[nid] = {
                "id": nid,
                "type": hit.get("type"),
                "heading": None,
                "snippet": hit.get("snippet", ""),
                "via": ["grep"],
            }

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [{**payload[nid], "score": round(score, 4)} for nid, score in ranked]


def _snippet(content: str, limit: int = 240) -> str:
    text = content.strip().replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


# ---------- bootstrap ----------


def ensure_indexed() -> dict | None:
    """If the vector store is empty, do a full reindex. Returns stats or None."""
    conn = _db()
    (count,) = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
    if count == 0:
        return reindex_all(prune=False)
    return None
