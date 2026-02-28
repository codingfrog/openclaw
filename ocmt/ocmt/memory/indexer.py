"""Memory indexer: scans markdown files, chunks, and indexes into SQLite.

Ported from OpenClaw's MemoryIndexManager.sync() in src/memory/manager.ts.
Uses hash-based change detection to skip unchanged files.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import time
from pathlib import Path

from ..config import MemoryConfig
from ..tenants.types import TenantWorkspace
from .chunker import chunk_markdown
from .schema import ensure_memory_schema
from .store import list_memory_files

logger = logging.getLogger(__name__)


class MemoryIndexer:
    """Index markdown memory files into SQLite for search.

    Each tenant gets an isolated SQLite database. The indexer:
    1. Scans for .md files in the workspace
    2. Detects changes via content hash
    3. Chunks changed files
    4. Updates SQLite (chunks table + FTS5)
    """

    def __init__(
        self,
        workspace: TenantWorkspace,
        config: MemoryConfig | None = None,
    ) -> None:
        self.workspace = workspace
        self.config = config or MemoryConfig()
        self.db = sqlite3.connect(str(workspace.memory_db))
        self.db.row_factory = sqlite3.Row
        schema_info = ensure_memory_schema(self.db)
        self.fts_available = schema_info.get("fts_available", False)
        self._dirty = True

    def sync(self, force: bool = False) -> int:
        """Scan and index all memory files. Returns count of files updated.

        Mirrors OpenClaw's MemoryIndexManager.sync() pattern:
        - Hash-based change detection
        - Re-chunk only changed files
        - Update FTS5 in sync
        """
        files = list_memory_files(self.workspace)
        updated = 0

        for file_path in files:
            try:
                if self._index_file(file_path, force=force):
                    updated += 1
            except Exception:
                logger.exception("Failed to index %s", file_path)

        # Clean up deleted files
        self._prune_deleted(files)

        self.db.commit()
        self._dirty = False
        logger.debug("Memory sync complete: %d files updated", updated)
        return updated

    def _index_file(self, file_path: Path, force: bool = False) -> bool:
        """Index a single file. Returns True if the file was updated."""
        content = file_path.read_text(encoding="utf-8")
        file_hash = hashlib.sha256(content.encode()).hexdigest()

        # Resolve relative path within workspace
        try:
            rel_path = str(file_path.relative_to(self.workspace.root / "workspace"))
        except ValueError:
            rel_path = str(file_path.relative_to(self.workspace.root))

        # Check if file changed (hash-based, from manager.ts sync logic)
        if not force:
            existing = self.db.execute(
                "SELECT hash FROM files WHERE path = ?", (rel_path,)
            ).fetchone()
            if existing and existing["hash"] == file_hash:
                return False

        # Chunk the content
        chunks = chunk_markdown(
            content,
            tokens_per_chunk=self.config.chunk_tokens,
            overlap_tokens=self.config.chunk_overlap,
        )

        # Delete old chunks for this file
        if self.fts_available:
            self.db.execute(
                "DELETE FROM chunks_fts WHERE path = ?", (rel_path,)
            )
        self.db.execute("DELETE FROM chunks WHERE path = ?", (rel_path,))

        # Insert new chunks
        now = int(time.time())
        for chunk in chunks:
            chunk_id = hashlib.sha256(
                f"{rel_path}:{chunk.start_line}:{chunk.end_line}:{file_hash}".encode()
            ).hexdigest()[:16]

            self.db.execute(
                """INSERT OR REPLACE INTO chunks
                   (id, path, start_line, end_line, hash, text, embedding, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (chunk_id, rel_path, chunk.start_line, chunk.end_line,
                 file_hash, chunk.text, now),
            )

            # Update FTS5
            if self.fts_available:
                self.db.execute(
                    """INSERT INTO chunks_fts (text, id, path, start_line, end_line)
                       VALUES (?, ?, ?, ?, ?)""",
                    (chunk.text, chunk_id, rel_path, chunk.start_line, chunk.end_line),
                )

        # Update files table
        stat = file_path.stat()
        self.db.execute(
            """INSERT OR REPLACE INTO files (path, hash, mtime, size)
               VALUES (?, ?, ?, ?)""",
            (rel_path, file_hash, int(stat.st_mtime), stat.st_size),
        )

        logger.debug("Indexed %s: %d chunks", rel_path, len(chunks))
        return True

    def _prune_deleted(self, existing_files: list[Path]) -> None:
        """Remove index entries for files that no longer exist."""
        existing_paths: set[str] = set()
        for fp in existing_files:
            try:
                existing_paths.add(str(fp.relative_to(self.workspace.root / "workspace")))
            except ValueError:
                existing_paths.add(str(fp.relative_to(self.workspace.root)))

        indexed = self.db.execute("SELECT path FROM files").fetchall()
        for row in indexed:
            if row["path"] not in existing_paths:
                self.db.execute("DELETE FROM chunks WHERE path = ?", (row["path"],))
                if self.fts_available:
                    self.db.execute(
                        "DELETE FROM chunks_fts WHERE path = ?", (row["path"],)
                    )
                self.db.execute("DELETE FROM files WHERE path = ?", (row["path"],))
                logger.debug("Pruned deleted file: %s", row["path"])

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    def mark_dirty(self) -> None:
        self._dirty = True

    def status(self) -> dict:
        """Return index status info."""
        files = self.db.execute("SELECT COUNT(*) as c FROM files").fetchone()
        chunks = self.db.execute("SELECT COUNT(*) as c FROM chunks").fetchone()
        return {
            "files": files["c"] if files else 0,
            "chunks": chunks["c"] if chunks else 0,
            "fts_available": self.fts_available,
            "dirty": self._dirty,
            "db_path": str(self.workspace.memory_db),
        }

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()
