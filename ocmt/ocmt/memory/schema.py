"""Per-tenant SQLite memory index schema.

Directly ported from OpenClaw's src/memory/memory-schema.ts.
Each tenant gets an isolated SQLite database for memory indexing.
"""

from __future__ import annotations

import sqlite3


def ensure_memory_schema(db: sqlite3.Connection) -> dict[str, bool]:
    """Create the memory index tables if they don't exist.

    Returns a dict with status flags (e.g. fts_available).

    Schema mirrors OpenClaw's ensureMemoryIndexSchema():
    - meta: key-value metadata
    - files: tracked markdown files with hash for change detection
    - chunks: chunked text with optional embeddings
    - chunks_fts: FTS5 virtual table for BM25 keyword search
    - embedding_cache: avoid re-encoding identical content
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            hash TEXT NOT NULL,
            mtime INTEGER NOT NULL,
            size INTEGER NOT NULL
        )
    """)

    db.execute("""
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            hash TEXT NOT NULL,
            text TEXT NOT NULL,
            embedding BLOB,
            updated_at INTEGER NOT NULL
        )
    """)
    db.execute("CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path)")

    # FTS5 for BM25 keyword search
    fts_available = False
    fts_error = None
    try:
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                text,
                id UNINDEXED,
                path UNINDEXED,
                start_line UNINDEXED,
                end_line UNINDEXED
            )
        """)
        fts_available = True
    except Exception as exc:
        fts_error = str(exc)

    db.execute("""
        CREATE TABLE IF NOT EXISTS embedding_cache (
            text_hash TEXT PRIMARY KEY,
            embedding BLOB NOT NULL,
            dims INTEGER,
            updated_at REAL NOT NULL
        )
    """)

    db.commit()
    return {"fts_available": fts_available, "fts_error": fts_error}
