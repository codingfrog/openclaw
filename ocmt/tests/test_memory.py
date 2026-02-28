"""Tests for memory store, chunker, indexer, and search."""

import datetime
from pathlib import Path

import pytest

from ocmt.memory.chunker import Chunk, chunk_markdown
from ocmt.memory.indexer import MemoryIndexer
from ocmt.memory.schema import ensure_memory_schema
from ocmt.memory.search import MemorySearch, _build_fts_query, _extract_keywords
from ocmt.memory.store import (
    append_daily_log,
    daily_log_path,
    list_memory_files,
    load_bootstrap_memory,
    read_daily_log,
    read_long_term_memory,
)
from ocmt.tenants.isolation import init_workspace
from ocmt.tenants.types import TenantWorkspace


@pytest.fixture
def workspace(tmp_path):
    ws = TenantWorkspace(tenant_id="test", root=tmp_path / "test-tenant")
    init_workspace(ws)
    return ws


class TestMemoryStore:
    def test_daily_log_path(self, workspace):
        date = datetime.date(2026, 2, 28)
        path = daily_log_path(workspace, date)
        assert path.name == "2026-02-28.md"

    def test_append_and_read_daily_log(self, workspace):
        date = datetime.date(2026, 2, 28)
        append_daily_log(workspace, "First entry", date)
        content = read_daily_log(workspace, date)
        assert content is not None
        assert "First entry" in content
        assert "2026-02-28" in content

        # Append more
        append_daily_log(workspace, "Second entry", date)
        content = read_daily_log(workspace, date)
        assert "First entry" in content
        assert "Second entry" in content

    def test_read_nonexistent_daily_log(self, workspace):
        date = datetime.date(2020, 1, 1)
        assert read_daily_log(workspace, date) is None

    def test_read_long_term_memory(self, workspace):
        content = read_long_term_memory(workspace)
        assert content is not None
        assert "Long-term Memory" in content

    def test_load_bootstrap_memory(self, workspace):
        # Write today's log
        append_daily_log(workspace, "Today's note")

        result = load_bootstrap_memory(workspace)
        assert result["long_term"] is not None
        assert result["daily_today"] is not None
        assert "Today's note" in result["daily_today"]

    def test_list_memory_files(self, workspace):
        append_daily_log(workspace, "Note 1", datetime.date(2026, 2, 27))
        append_daily_log(workspace, "Note 2", datetime.date(2026, 2, 28))

        files = list_memory_files(workspace)
        # Should include MEMORY.md + 2 daily logs
        assert len(files) >= 3


class TestChunker:
    def test_empty_content(self):
        assert chunk_markdown("") == []
        assert chunk_markdown("   ") == []

    def test_single_small_section(self):
        content = "# Hello\n\nThis is a short note."
        chunks = chunk_markdown(content)
        assert len(chunks) == 1
        assert chunks[0].start_line == 1
        assert "Hello" in chunks[0].text

    def test_multiple_sections(self):
        content = "\n".join([
            "# Section 1",
            "Content for section one.",
            "",
            "## Section 2",
            "Content for section two.",
            "",
            "## Section 3",
            "Content for section three.",
        ])
        chunks = chunk_markdown(content)
        assert len(chunks) >= 2

    def test_long_section_gets_split(self):
        # Create a section with many words
        long_text = "word " * 500  # ~500 words = ~650 tokens
        content = f"# Long Section\n\n{long_text}"
        chunks = chunk_markdown(content, tokens_per_chunk=200)
        assert len(chunks) > 1


class TestFtsHelpers:
    def test_build_fts_query(self):
        q = _build_fts_query("hello world")
        assert q is not None
        assert "hello" in q
        assert "world" in q

    def test_build_fts_query_empty(self):
        assert _build_fts_query("") is None
        assert _build_fts_query("!!!") is None

    def test_extract_keywords(self):
        keywords = _extract_keywords("what did we discuss about the API yesterday")
        assert "api" in keywords
        assert "discuss" in keywords
        # Stop words should be filtered
        assert "the" not in keywords
        assert "about" not in keywords


class TestIndexerAndSearch:
    def test_index_and_search(self, workspace):
        # Write some memory
        append_daily_log(workspace, "We decided to use PostgreSQL for the database.", datetime.date(2026, 2, 28))
        append_daily_log(workspace, "The API endpoint should use REST.", datetime.date(2026, 2, 27))

        # Create indexer and sync
        indexer = MemoryIndexer(workspace)
        updated = indexer.sync()
        assert updated > 0

        status = indexer.status()
        assert status["files"] > 0
        assert status["chunks"] > 0

        # Search
        search = MemorySearch(indexer)
        results = search.search("PostgreSQL database")
        assert len(results) > 0
        assert any("PostgreSQL" in r.snippet for r in results)

        # Search for something else
        results2 = search.search("API REST endpoint")
        assert len(results2) > 0

        indexer.close()

    def test_index_is_idempotent(self, workspace):
        append_daily_log(workspace, "Test content")

        indexer = MemoryIndexer(workspace)
        updated1 = indexer.sync()
        assert updated1 > 0

        # Second sync should not update unchanged files
        updated2 = indexer.sync()
        assert updated2 == 0

        indexer.close()

    def test_read_file(self, workspace):
        append_daily_log(workspace, "Line one\nLine two\nLine three")

        indexer = MemoryIndexer(workspace)
        search = MemorySearch(indexer)

        result = search.read_file("MEMORY.md")
        assert result["text"] != ""
        assert result["path"] == "MEMORY.md"

        indexer.close()


class TestMemorySchema:
    def test_ensure_schema(self, tmp_path):
        import sqlite3
        db = sqlite3.connect(str(tmp_path / "test.sqlite"))
        result = ensure_memory_schema(db)
        assert result["fts_available"] is True

        # Verify tables exist
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {t[0] for t in tables}
        assert "meta" in table_names
        assert "files" in table_names
        assert "chunks" in table_names

        db.close()
