"""Local vector search over the vault using sqlite-vec + fastembed (multilingual-e5-large)."""
from __future__ import annotations

import hashlib
import logging
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

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
EMBED_MODEL = os.environ.get("BRAIN_EMBED_MODEL", "intfloat/multilingual-e5-large")
EMBED_DIM = int(os.environ.get("BRAIN_EMBED_DIM", "1024"))
# e5 models expect explicit "query:" / "passage:" prefixes; auto-applied when model name starts with "intfloat/".
_E5_FAMILY = EMBED_MODEL.startswith("intfloat/")
DB_PATH = Path(os.environ.get("BRAIN_VECTOR_DB", str(REPO_ROOT / ".vectors.db")))
# Persist the embedding model outside the OS temp dir — macOS purges
# /var/folders/.../T, which silently corrupts the ~2GB onnx external-data file
# and leaves reindex skipping vectors. Overridable via BRAIN_EMBED_CACHE or
# fastembed's own FASTEMBED_CACHE_PATH.
EMBED_CACHE_DIR = Path(
    os.environ.get("BRAIN_EMBED_CACHE")
    or os.environ.get("FASTEMBED_CACHE_PATH")
    or (Path.home() / ".cache" / "fastembed")
).expanduser()
MIN_CHUNK_CHARS = 40
# multilingual-e5-large truncates at ~512 tokens (~2 KB). Stay safely under that
# limit so each embedded chunk carries retrievable text, not a silent prefix cut.
MAX_CHUNK_CHARS = 1400
# When a logical section splits into multiple sub-chunks, section_idx becomes
# (base_idx + 1) * _SUB_IDX_SCALE + sub_idx so indices stay stable and unique.
_SUB_IDX_SCALE = 1000
# Overlap between sliding-window sub-chunks (chars) to avoid boundary clipping.
_SPLIT_OVERLAP = 100
# Header metadata is capped so a long alias/tag list cannot consume the embed budget.
_MAX_HEADER_CHARS = MAX_CHUNK_CHARS // 4
# Backstop: refuse to emit more sub-chunks than this per logical section.
_MAX_SUB_CHUNKS = 512
# Backstop: cap total chunks per note across all sections (bounds one _embed batch).
_MAX_CHUNKS_PER_NOTE = 2048

H2_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
H3_RE = re.compile(r"^###\s+", re.MULTILINE)


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
    # WAL lets the external reindex process read concurrently without lock errors.
    # Keep the default check_same_thread=True: FastMCP dispatches sync tools inline
    # on the event-loop thread, so the cached connection stays single-threaded — and
    # if that ever changes, we want a loud error, not silent disabling of the guard.
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
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

    EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=EMBED_MODEL, cache_dir=str(EMBED_CACHE_DIR))


def _embed(texts: list[str], *, kind: str = "passage") -> list[list[float]]:
    if _E5_FAMILY:
        prefix = "query: " if kind == "query" else "passage: "
        texts = [prefix + t for t in texts]
    return [list(v) for v in _embedder().embed(texts)]


def _to_blob(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


# ---------- chunking ----------


def _sliding_window(text: str, *, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split *text* into fixed-size windows with overlap."""
    overlap = min(_SPLIT_OVERLAP, max(0, limit // 4))
    step = max(1, limit - overlap)
    parts: list[str] = []
    start = 0
    while start < len(text):
        parts.append(text[start : start + limit])
        if start + limit >= len(text):
            break
        start += step
    return parts


def _cap_header_blob(header_blob: str) -> str:
    """Keep header metadata within a fixed fraction of ``MAX_CHUNK_CHARS``."""
    if len(header_blob) <= _MAX_HEADER_CHARS:
        return header_blob
    return header_blob[: _MAX_HEADER_CHARS - 1] + "…"


def _split_on_pattern(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Split *text* on lines matching *pattern*, keeping the delimiter line."""
    matches = list(pattern.finditer(text))
    if not matches:
        return [text]
    parts: list[str] = []
    if matches[0].start() > 0:
        parts.append(text[: matches[0].start()].strip())
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[m.start() : end].strip())
    return [p for p in parts if p]


def _pack_paragraphs(text: str, *, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Greedy paragraph packing up to *limit* chars."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return [text]
    parts: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}".strip() if current else para
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                parts.append(current)
            if len(para) <= limit:
                current = para
            else:
                parts.extend(_sliding_window(para, limit=limit))
                current = ""
    if current:
        parts.append(current)
    return parts


def _split_oversized(text: str, *, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Sub-split *text* when it exceeds *limit* (H3 → paragraph → window)."""
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    for h3_part in _split_on_pattern(text, H3_RE):
        if len(h3_part) <= limit:
            parts.append(h3_part)
        else:
            parts.extend(_pack_paragraphs(h3_part, limit=limit))

    # Both producers above already guarantee every part <= limit
    # (_pack_paragraphs falls back to _sliding_window internally), so the old
    # re-split fallback here was dead. Keep a hard backstop against a future
    # producer regression — raise (not assert) to match _section_indices' guard
    # style and survive `python -O`, rather than re-splitting a bounded part.
    oversized = [len(part) for part in parts if len(part) > limit]
    if oversized:
        raise RuntimeError(
            f"_split_oversized producer emitted {len(oversized)} part(s) over "
            f"limit {limit}: sizes {oversized}"
        )
    return parts


def _section_indices(
    base_idx: int,
    num_parts: int,
    *,
    note_id: str = "",
    max_legacy_single: int = 0,
) -> list[int]:
    """Map sub-parts to stable ``section_idx`` values (respects UNIQUE per note)."""
    prefix = f"note {note_id!r}: " if note_id else ""
    # Not load-bearing on the production path: _emit_chunks caps body_parts at
    # _MAX_SUB_CHUNKS (512) < _SUB_IDX_SCALE (1000) before calling here, so this
    # raise is only reachable via direct/test calls. Kept as a hard invariant guard.
    if num_parts >= _SUB_IDX_SCALE:
        raise RuntimeError(
            f"{prefix}cannot split section {base_idx} into {num_parts} sub-chunks "
            f"(limit is {_SUB_IDX_SCALE - 1})"
        )
    if num_parts == 1:
        return [base_idx]
    sub_start = (base_idx + 1) * _SUB_IDX_SCALE
    sub_end = sub_start + num_parts - 1
    if sub_start <= max_legacy_single:
        raise RuntimeError(
            f"{prefix}sub-chunk indices [{sub_start}, {sub_end}] collide with legacy "
            f"single-chunk indices up to {max_legacy_single}"
        )
    return [sub_start + i for i in range(num_parts)]


def _emit_chunks(
    note_id: str,
    base_idx: int,
    heading: str,
    header_blob: str,
    body_text: str,
    *,
    max_legacy_single: int = 0,
) -> list[Chunk]:
    """Build one or more chunks for a logical section, sub-splitting when needed."""
    body_text = body_text.strip()
    header_blob = _cap_header_blob(header_blob)
    header_prefix = f"{header_blob}\n\n"
    full = f"{header_prefix}{body_text}".strip()
    if len(full) < MIN_CHUNK_CHARS:
        return []

    max_body = max(MIN_CHUNK_CHARS, MAX_CHUNK_CHARS - len(header_prefix))
    if len(full) <= MAX_CHUNK_CHARS:
        body_parts = [body_text]
    else:
        body_parts = _split_oversized(body_text, limit=max_body)
        if len(body_parts) > _MAX_SUB_CHUNKS:
            dropped = len(body_parts) - _MAX_SUB_CHUNKS
            logger.warning(
                "note %r section %r: capped at %d sub-chunks; dropping %d trailing part(s)",
                note_id,
                heading,
                _MAX_SUB_CHUNKS,
                dropped,
            )
            body_parts = body_parts[:_MAX_SUB_CHUNKS]

    indices = _section_indices(
        base_idx,
        len(body_parts),
        note_id=note_id,
        max_legacy_single=max_legacy_single,
    )
    return [
        Chunk(
            note_id=note_id,
            section_idx=idx,
            heading=heading,
            content=f"{header_prefix}{part}".strip(),
        )
        for idx, part in zip(indices, body_parts)
    ]


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
        text = body.strip()
        if text or header_blob:
            chunks.extend(
                _emit_chunks(note.id, 0, title, header_blob, text, max_legacy_single=0)
            )
        return _cap_note_chunks(note.id, chunks)

    max_legacy = len(matches)
    preamble = body[: matches[0].start()].strip()
    chunks.extend(
        _emit_chunks(
            note.id, 0, title, header_blob, preamble, max_legacy_single=max_legacy
        )
    )

    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        section_text = body[m.start() : end].strip()
        heading = m.group(1).strip()
        chunks.extend(
            _emit_chunks(
                note.id,
                i + 1,
                heading,
                header_blob,
                section_text,
                max_legacy_single=max_legacy,
            )
        )
    return _cap_note_chunks(note.id, chunks)


def _cap_note_chunks(note_id: str, chunks: list[Chunk]) -> list[Chunk]:
    """Apply the per-note aggregate chunk ceiling."""
    if len(chunks) <= _MAX_CHUNKS_PER_NOTE:
        return chunks
    dropped = len(chunks) - _MAX_CHUNKS_PER_NOTE
    logger.warning(
        "note %r: aggregate chunk cap %d exceeded (%d total); dropping %d trailing chunk(s)",
        note_id,
        _MAX_CHUNKS_PER_NOTE,
        len(chunks),
        dropped,
    )
    return chunks[:_MAX_CHUNKS_PER_NOTE]


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
                new_id = cur.lastrowid
                if new_id is None:
                    raise RuntimeError("INSERT into chunks failed to return rowid")
                chunk_id = new_id
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
    totals: dict = {"notes": 0, "embedded": 0, "deleted": 0, "chunks": 0, "failed": []}

    for note in iter_notes():
        live_ids.add(note.id)
        try:
            result = reindex_note(note.id)
        except Exception as exc:
            conn.rollback()
            logger.error("reindex failed for %s: %s", note.id, exc)
            totals["failed"].append({"note_id": note.id, "error": str(exc)})
            continue
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


# search_semantic over-fetches: dedup-by-note_id shrinks the KNN row list, and the
# type filter is applied post-fetch, so both paths need headroom to still yield k.
_OVERFETCH_FACTOR = 4


def search_semantic(
    query: str,
    k: int = 10,
    type_filter: str | None = None,
) -> list[dict]:
    conn = _db()
    qvec = _embed([query], kind="query")[0]
    fetch = k * _OVERFETCH_FACTOR
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

    from . import vault

    _, _, meta = vault._graph()  # cached; avoids a file read per KNN row for type

    out: list[dict] = []
    seen: set[str] = set()
    for note_id, section_idx, heading, content, distance in rows:
        if note_id in seen:
            continue
        if note_id in meta:
            note_type = meta[note_id]["type"]
        else:
            # archived / stale chunk not in the active graph — fall back to a read
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


# Max connective notes (graph-only, not text hits) admitted as candidates,
# as a multiple of k. Keeps the final result set bounded; PPR itself always
# scans the whole graph regardless.
_CONNECTIVE_FACTOR = 2

# Standard RRF constant (Cormack & Clarke, 2009): smooths rank reciprocals so a
# #1 hit scores 1/61 rather than 1.0, bounding any single ranker's influence.
_RRF_K = 60


def _fuse_rrf(sem: list[dict], grep: list[dict]) -> tuple[dict[str, float], dict[str, dict]]:
    """Reciprocal-rank fusion of semantic + grep hits.

    Returns ``(text_scores, payload)``: fused relevance per note id, and a result
    payload carrying provenance (``via``: semantic / grep / both).
    """
    text_scores: dict[str, float] = {}
    payload: dict[str, dict] = {}
    for rank, hit in enumerate(sem):
        nid = hit["id"]
        text_scores[nid] = text_scores.get(nid, 0.0) + 1.0 / (_RRF_K + rank + 1)
        payload[nid] = {
            "id": nid,
            "type": hit.get("type"),
            "heading": hit.get("heading"),
            "snippet": hit["snippet"],
            "via": ["semantic"],
        }
    for rank, hit in enumerate(grep):
        nid = hit["id"]
        text_scores[nid] = text_scores.get(nid, 0.0) + 1.0 / (_RRF_K + rank + 1)
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
    return text_scores, payload


def _graph_hit_payload(nid: str, note_meta: dict[str, dict]) -> dict | None:
    """Base result dict for a note surfaced by the graph (not a text hit).

    Returns ``None`` if the note vanished between the graph snapshot and this read
    (a write landed mid-query). Callers add any extra keys (source, score, …).
    """
    from . import vault

    note = vault.find_note_by_id(nid)
    if note is None:
        return None
    return {
        "id": nid,
        "type": note_meta.get(nid, {}).get("type"),
        "heading": None,
        "snippet": vault._first_line(note.body),
        "via": ["graph"],
    }


def search_hybrid(
    query: str,
    k: int = 10,
    type_filter: str | None = None,
    structural_weight: float = 0.3,
) -> list[dict]:
    """Reciprocal-rank fusion of semantic + grep, re-ranked by graph proximity.

    Stage 1 fuses semantic and grep hits by reciprocal rank (the text signal —
    these are the *entry points*). Stage 2, when ``structural_weight > 0``, runs a
    personalized PageRank over the wikilink graph seeded on those hits (weighted by
    their text score) and blends it in:

        score = text_norm + structural_weight * ppr_norm

    where both terms are normalized to ``[0, 1]``. This does two things the old
    static-centrality nudge could not: it is *query-aware* (proximity to the hits,
    not global popularity), and it can surface a **connective note** — one that
    matches neither the text nor the embedding query but sits between several strong
    hits. ``structural_weight`` controls the lean; raise it to trust the graph more.

    Pass ``structural_weight=0`` to rank on pure text relevance — identical ordering
    to the semantic+grep fusion alone, with no graph computation.
    """
    from . import vault

    if k <= 0:
        return []

    sem = search_semantic(query, k=k, type_filter=type_filter)
    grep = vault.search_notes(query, type_filter, None, k)

    text_scores, payload = _fuse_rrf(sem, grep)

    if not structural_weight or not text_scores:
        ranked = sorted(text_scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
        return [{**payload[nid], "score": round(score, 4)} for nid, score in ranked]

    ppr = vault.personalized_pagerank(text_scores)
    meta = vault._graph()[2]

    # Connective candidates: notes the graph lifts but that the text never found.
    # Take the highest-PPR such notes (bounded), respecting the type filter.
    connective = sorted(
        (
            (nid, score)
            for nid, score in ppr.items()
            if nid not in text_scores
            and score > 0.0
            and (not type_filter or meta.get(nid, {}).get("type") == type_filter)
        ),
        key=lambda kv: kv[1],
        reverse=True,
    )[: k * _CONNECTIVE_FACTOR]

    text_max = max(text_scores.values())
    candidates = set(text_scores) | {nid for nid, _ in connective}
    ppr_max = max((ppr.get(nid, 0.0) for nid in candidates), default=0.0)

    scores: dict[str, float] = {}
    for nid in candidates:
        text_norm = text_scores.get(nid, 0.0) / text_max
        ppr_norm = ppr.get(nid, 0.0) / ppr_max if ppr_max else 0.0
        scores[nid] = text_norm + structural_weight * ppr_norm

    for nid, _ in connective:
        hit_payload = _graph_hit_payload(nid, meta)
        if hit_payload is None:
            scores.pop(nid, None)
            continue
        payload[nid] = hit_payload

    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:k]
    return [{**payload[nid], "score": round(score, 4)} for nid, score in ranked]


def search_graph(
    query: str,
    k: int = 10,
    type_filter: str | None = None,
    neighbors_per_seed: int = 5,
    edge_factor: float = 0.5,
) -> list[dict]:
    """Hybrid search, then expand each seed with its 1-hop wikilink neighbors.

    Returns "the note AND its context": seeds ranked by hybrid relevance, plus the
    notes one wikilink away (outbound + backlinks), scored as ``seed_score *
    edge_factor`` and accumulated when reachable from multiple seeds. Bounded to
    one hop, ``neighbors_per_seed`` per seed, and at most ``k`` total results.

    **Seed protection:** direct hybrid hits (seeds) are never dropped in favor of
    graph-only neighbors. When truncating to ``k``, all seeds are kept (top ``k``
    by score if there are more than ``k`` seeds); any remaining slots are filled
    with the highest-scoring graph neighbors. The returned list is then ordered by
    ``score`` descending, consistent with ``search_semantic`` / ``search_hybrid``.

    Each result carries ``source`` ("seed" | "graph"); graph neighbors also carry
    ``neighbor_of`` (the seed ids that pulled them in).
    """
    from . import vault

    if k <= 0:
        return []

    seeds = search_hybrid(query, k=k, type_filter=type_filter)
    results: dict[str, dict] = {hit["id"]: {**hit, "source": "seed"} for hit in seeds}

    # One cached adjacency build for all seeds, instead of a full vault scan per
    # seed via links_of. out/inn already exclude dangling links.
    out_edges, in_edges, meta = vault._graph()

    # Deterministic neighbor ordering: highest-degree first, id as tiebreak. Closes
    # over the loop-invariant edge maps only, so it is defined once, not per seed.
    def _neighbor_key(nid: str) -> tuple[int, str]:
        degree = len(out_edges.get(nid, ())) + len(in_edges.get(nid, ()))
        return (-degree, nid)

    neighbor_scores: dict[str, float] = {}
    neighbor_of: dict[str, list[str]] = {}
    for seed in seeds:
        sid = seed["id"]
        contribution = seed["score"] * edge_factor
        outbound = out_edges.get(sid, frozenset())
        inbound = in_edges.get(sid, frozenset())
        candidates = (outbound | inbound) - {sid}

        picked = sorted(candidates, key=_neighbor_key)[:neighbors_per_seed]

        for nid in picked:
            neighbor_scores[nid] = neighbor_scores.get(nid, 0.0) + contribution
            neighbor_of.setdefault(nid, []).append(sid)

    graph_items: list[dict] = []
    for nid, score in neighbor_scores.items():
        if nid in results:
            # already surfaced as a seed — annotate provenance, keep its seed score
            results[nid].setdefault("neighbor_of", []).extend(neighbor_of[nid])
            continue
        ntype = meta.get(nid, {}).get("type")
        if type_filter and ntype != type_filter:
            continue
        hit_payload = _graph_hit_payload(nid, meta)
        if hit_payload is None:
            continue
        graph_items.append(
            {
                **hit_payload,
                "source": "graph",
                "neighbor_of": neighbor_of[nid],
                "score": round(score, 4),
            }
        )

    graph_items.sort(key=lambda d: d["score"], reverse=True)
    seed_list = sorted(results.values(), key=lambda d: d["score"], reverse=True)
    # Defensive: search_hybrid already returns <= k seeds today, so this does not
    # trigger on the current path. Kept as the only guard if that contract ever
    # changes to over-return — seeds must still respect the k ceiling.
    if len(seed_list) > k:
        seed_list = seed_list[:k]
    remaining = k - len(seed_list)
    if remaining > 0:
        seed_list.extend(graph_items[:remaining])
    seed_list.sort(key=lambda d: d["score"], reverse=True)
    return seed_list


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
