"""Test harness: build a throwaway vault and point brain_mcp at it.

`vault.py` resolves VAULT_PATH at import time, so the env vars must be set here —
at conftest import, before any `brain_mcp` module is imported by a test.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import yaml

_VAULT = Path(tempfile.mkdtemp(prefix="brain-test-vault-"))

for _d in (
    "notes", "daily", "meetings", "conversations", "_archive",
    "_index", "_system/recipes", "_system/scripts",
):
    (_VAULT / _d).mkdir(parents=True, exist_ok=True)

(_VAULT / "_system" / "CLAUDE.md").write_text("# Doctrine\nWrite conventions.\n", encoding="utf-8")
for _moc in ("people", "projects", "topics", "timeline", "tags", "README"):
    (_VAULT / "_index" / f"{_moc}.md").write_text(f"# {_moc}\n", encoding="utf-8")

(_VAULT / "_system" / "recipes" / "task.md").write_text(
    "---\n"
    "kind: task\n"
    "class: living-list\n"
    "description: test task\n"
    "fields:\n"
    "  required: [title]\n"
    "  optional: [project]\n"
    "retrieval:\n"
    "  filters: [project]\n"
    "states: [open, done]\n"
    "default_state: open\n"
    "terminal_states: [done]\n"
    "---\n"
    "Task recipe body.\n",
    encoding="utf-8",
)
(_VAULT / "_system" / "recipes" / "sample-workflow.md").write_text(
    "# Sample Workflow\nA multi-step procedure (no kind/class).\n", encoding="utf-8"
)

os.environ["VAULT_PATH"] = str(_VAULT)
os.environ["BRAIN_VECTOR_DB"] = str(_VAULT / ".vectors.db")

import pytest  # noqa: E402


def _clear_graph_cache() -> None:
    import brain_mcp.vault as v
    v._graph_cached.cache_clear()


@pytest.fixture
def vault_root() -> Path:
    return _VAULT


@pytest.fixture
def write_note():
    """Write a note into the temp vault and invalidate the cached graph."""
    created: list[Path] = []

    def _w(folder: str, stem: str, frontmatter: dict | None = None, body: str = "") -> Path:
        text = "---\n" + yaml.safe_dump(frontmatter or {}, sort_keys=False, allow_unicode=True) + "---\n" + body
        path = _VAULT / folder / f"{stem}.md"
        path.write_text(text, encoding="utf-8")
        created.append(path)
        _clear_graph_cache()
        return path

    yield _w

    for path in created:
        path.unlink(missing_ok=True)
    _clear_graph_cache()


@pytest.fixture
def stub_reindex(monkeypatch):
    """Neutralize reindex so write tests never load the embedding model."""
    import brain_mcp.writes as writes
    monkeypatch.setattr(writes, "run_reindex", lambda note_id=None: "[reindex: stubbed]")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_VAULT, ignore_errors=True)
