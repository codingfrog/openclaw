"""Hybrid search: BM25 + vector merge, MMR, temporal decay.

Ported from OpenClaw's:
- src/memory/hybrid.ts (mergeHybridResults)
- src/memory/mmr.ts (Maximal Marginal Relevance)
- src/memory/temporal-decay.ts (recency weighting)

These are enhancement modules — the system works with FTS-only
search initially. Add vector search by plugging in an embedding provider.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path

from .search import MemorySearchResult


def merge_hybrid_results(
    vector: list[MemorySearchResult],
    keyword: list[MemorySearchResult],
    vector_weight: float = 0.7,
    text_weight: float = 0.3,
) -> list[MemorySearchResult]:
    """Merge vector and keyword search results with weighted scoring.

    Mirrors OpenClaw's mergeHybridResults from src/memory/hybrid.ts.
    Default weights: 70% vector, 30% text.
    """
    # Normalize scores within each result set to [0, 1]
    vector_norm = _normalize_scores(vector)
    keyword_norm = _normalize_scores(keyword)

    # Build combined score map
    combined: dict[str, tuple[MemorySearchResult, float]] = {}

    for r in vector_norm:
        combined[r.chunk_id] = (r, r.score * vector_weight)

    for r in keyword_norm:
        if r.chunk_id in combined:
            existing_result, existing_score = combined[r.chunk_id]
            combined[r.chunk_id] = (existing_result, existing_score + r.score * text_weight)
        else:
            combined[r.chunk_id] = (r, r.score * text_weight)

    # Sort by combined score
    results = []
    for result, score in combined.values():
        results.append(MemorySearchResult(
            path=result.path,
            start_line=result.start_line,
            end_line=result.end_line,
            score=score,
            snippet=result.snippet,
            chunk_id=result.chunk_id,
        ))

    results.sort(key=lambda r: r.score, reverse=True)
    return results


def _normalize_scores(results: list[MemorySearchResult]) -> list[MemorySearchResult]:
    """Normalize scores to [0, 1] range."""
    if not results:
        return results

    max_score = max(r.score for r in results)
    min_score = min(r.score for r in results)
    score_range = max_score - min_score

    if score_range == 0:
        return [MemorySearchResult(
            path=r.path, start_line=r.start_line, end_line=r.end_line,
            score=1.0, snippet=r.snippet, chunk_id=r.chunk_id,
        ) for r in results]

    return [MemorySearchResult(
        path=r.path, start_line=r.start_line, end_line=r.end_line,
        score=(r.score - min_score) / score_range,
        snippet=r.snippet, chunk_id=r.chunk_id,
    ) for r in results]


def apply_temporal_decay(
    results: list[MemorySearchResult],
    half_life_days: int = 30,
    workspace_dir: str | None = None,
) -> list[MemorySearchResult]:
    """Apply temporal decay to boost recent memories.

    Ported from OpenClaw's src/memory/temporal-decay.ts.
    Uses exponential decay with configurable half-life.
    Extracts date from file path (memory/YYYY-MM-DD.md pattern).
    """
    now = datetime.now()
    decayed: list[MemorySearchResult] = []

    for r in results:
        date = _extract_date_from_path(r.path)
        if date:
            days_ago = (now - datetime.combine(date, datetime.min.time())).days
            # Exponential decay: score * 2^(-days/half_life)
            decay_factor = math.pow(2, -days_ago / half_life_days)
            new_score = r.score * (0.5 + 0.5 * decay_factor)  # Floor at 50%
        else:
            new_score = r.score

        decayed.append(MemorySearchResult(
            path=r.path, start_line=r.start_line, end_line=r.end_line,
            score=new_score, snippet=r.snippet, chunk_id=r.chunk_id,
        ))

    decayed.sort(key=lambda r: r.score, reverse=True)
    return decayed


def _extract_date_from_path(path: str) -> datetime | None:
    """Extract date from a memory file path like memory/2026-02-28.md."""
    match = re.search(r'(\d{4}-\d{2}-\d{2})', path)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y-%m-%d")
        except ValueError:
            pass
    return None


def apply_mmr(
    results: list[MemorySearchResult],
    lambda_param: float = 0.7,
) -> list[MemorySearchResult]:
    """Apply Maximal Marginal Relevance for diversity.

    Ported from OpenClaw's src/memory/mmr.ts.
    Reduces redundancy by penalizing results similar to already-selected ones.

    Uses simple text overlap as similarity measure (no embeddings needed).
    """
    if len(results) <= 1:
        return results

    selected: list[MemorySearchResult] = [results[0]]
    remaining = list(results[1:])

    while remaining:
        best_idx = -1
        best_mmr = -float("inf")

        for i, candidate in enumerate(remaining):
            relevance = candidate.score
            max_similarity = max(
                _text_similarity(candidate.snippet, s.snippet)
                for s in selected
            )
            mmr_score = lambda_param * relevance - (1 - lambda_param) * max_similarity

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        if best_idx >= 0:
            selected.append(remaining.pop(best_idx))
        else:
            break

    return selected


def _text_similarity(a: str, b: str) -> float:
    """Simple Jaccard similarity between two text snippets."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = len(words_a & words_b)
    union = len(words_a | words_b)
    return intersection / union if union > 0 else 0.0
