"""Memory search: BM25 keyword search via SQLite FTS5.

Ported from OpenClaw's src/memory/manager-search.ts (searchKeyword)
and src/memory/manager.ts (search method, FTS-only mode lines 257-289).

Starts with FTS-only mode (no embedding cost). Vector search can be
added later via the hybrid.py module.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

from ..config import MemoryConfig
from ..tenants.types import TenantWorkspace
from .indexer import MemoryIndexer

logger = logging.getLogger(__name__)

SNIPPET_MAX_CHARS = 700


@dataclass
class MemorySearchResult:
    """A search result from memory.

    Mirrors OpenClaw's MemorySearchResult from src/memory/types.ts.
    """

    path: str
    start_line: int
    end_line: int
    score: float
    snippet: str
    chunk_id: str = ""


def _build_fts_query(raw: str) -> str | None:
    """Build an FTS5 query from raw text.

    Mirrors OpenClaw's buildFtsQuery from src/memory/hybrid.ts.
    Strips punctuation and builds OR-joined terms.
    """
    # Remove special FTS5 characters
    cleaned = re.sub(r'[^\w\s]', ' ', raw)
    terms = [t.strip() for t in cleaned.split() if t.strip()]
    if not terms:
        return None
    # Join with OR for broader matching
    return " OR ".join(f'"{t}"' for t in terms)


def _extract_keywords(query: str) -> list[str]:
    """Extract meaningful keywords from a conversational query.

    Mirrors OpenClaw's extractKeywords from src/memory/query-expansion.ts.
    Filters out common stop words to improve FTS matching.
    """
    stop_words = {
        "a", "an", "the", "is", "was", "are", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "can", "shall",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "as", "into", "through", "during", "before", "after", "about",
        "between", "under", "above", "up", "down", "out", "off", "over",
        "that", "this", "these", "those", "it", "its", "we", "they",
        "them", "their", "our", "my", "your", "he", "she", "him", "her",
        "and", "but", "or", "nor", "not", "so", "if", "then", "than",
        "what", "which", "who", "whom", "how", "when", "where", "why",
        "i", "me", "you",
    }
    words = re.findall(r'\b\w+\b', query.lower())
    keywords = [w for w in words if w not in stop_words and len(w) > 1]
    return keywords


class MemorySearch:
    """Search over indexed memory using BM25 (FTS5).

    This is the FTS-only mode from OpenClaw's manager.ts lines 257-289.
    No embedding provider required — works entirely offline.
    """

    def __init__(
        self,
        indexer: MemoryIndexer,
        config: MemoryConfig | None = None,
    ) -> None:
        self.indexer = indexer
        self.config = config or MemoryConfig()

    def search(
        self,
        query: str,
        max_results: int | None = None,
        min_score: float | None = None,
    ) -> list[MemorySearchResult]:
        """Search memory using BM25 keyword matching.

        Mirrors the FTS-only search path in OpenClaw's manager.ts.
        """
        max_results = max_results or self.config.max_results
        min_score = min_score if min_score is not None else self.config.min_score

        # Sync if dirty
        if self.indexer.is_dirty:
            self.indexer.sync()

        if not self.indexer.fts_available:
            logger.warning("FTS5 not available, returning empty results")
            return []

        query = query.strip()
        if not query:
            return []

        # Extract keywords for better FTS matching on conversational queries
        keywords = _extract_keywords(query)
        search_terms = keywords if keywords else [query]

        # Search with each keyword and merge results
        seen: dict[str, MemorySearchResult] = {}
        for term in search_terms:
            results = self._search_keyword(term, max_results * 4)
            for r in results:
                existing = seen.get(r.chunk_id)
                if not existing or r.score > existing.score:
                    seen[r.chunk_id] = r

        merged = sorted(seen.values(), key=lambda r: r.score, reverse=True)
        return [r for r in merged if r.score >= min_score][:max_results]

    def _search_keyword(self, query: str, limit: int) -> list[MemorySearchResult]:
        """Execute a single FTS5 keyword search."""
        fts_query = _build_fts_query(query)
        if not fts_query:
            return []

        try:
            rows = self.indexer.db.execute(
                """
                SELECT
                    chunks_fts.id,
                    chunks_fts.path,
                    chunks_fts.start_line,
                    chunks_fts.end_line,
                    rank,
                    substr(chunks.text, 1, ?) as snippet
                FROM chunks_fts
                JOIN chunks ON chunks.id = chunks_fts.id
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (SNIPPET_MAX_CHARS, fts_query, limit),
            ).fetchall()
        except Exception:
            logger.debug("FTS query failed for: %s", fts_query)
            return []

        results: list[MemorySearchResult] = []
        for row in rows:
            # FTS5 rank is negative (lower = better match).
            # Convert to 0-1 score: score = 1 / (1 + abs(rank))
            raw_rank = abs(row["rank"]) if row["rank"] else 1.0
            score = 1.0 / (1.0 + raw_rank)

            results.append(MemorySearchResult(
                path=row["path"],
                start_line=row["start_line"],
                end_line=row["end_line"],
                score=score,
                snippet=row["snippet"] or "",
                chunk_id=row["id"],
            ))

        return results

    def read_file(
        self,
        rel_path: str,
        from_line: int | None = None,
        lines: int | None = None,
    ) -> dict[str, str]:
        """Read a memory file by relative path.

        Mirrors OpenClaw's MemoryIndexManager.readFile() from manager.ts.
        """
        workspace_dir = self.indexer.workspace.root / "workspace"
        abs_path = workspace_dir / rel_path

        # Security: validate path is within workspace
        try:
            abs_path.resolve().relative_to(workspace_dir.resolve())
        except ValueError:
            return {"text": "", "path": rel_path}

        if not abs_path.exists() or not abs_path.suffix == ".md":
            return {"text": "", "path": rel_path}

        content = abs_path.read_text(encoding="utf-8")

        if from_line is not None or lines is not None:
            content_lines = content.split("\n")
            start = max(0, (from_line or 1) - 1)
            count = lines or len(content_lines)
            content = "\n".join(content_lines[start:start + count])

        return {"text": content, "path": rel_path}
