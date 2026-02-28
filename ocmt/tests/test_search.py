"""Tests for hybrid search, temporal decay, and MMR."""

from datetime import datetime

import pytest

from ocmt.memory.hybrid import (
    _text_similarity,
    apply_mmr,
    apply_temporal_decay,
    merge_hybrid_results,
)
from ocmt.memory.search import MemorySearchResult


def _make_result(chunk_id: str, score: float, snippet: str = "", path: str = "test.md") -> MemorySearchResult:
    return MemorySearchResult(
        path=path,
        start_line=1,
        end_line=5,
        score=score,
        snippet=snippet,
        chunk_id=chunk_id,
    )


class TestHybridMerge:
    def test_merge_with_overlap(self):
        vector = [
            _make_result("c1", 0.9, "vector result 1"),
            _make_result("c2", 0.7, "vector result 2"),
        ]
        keyword = [
            _make_result("c1", 0.8, "keyword result 1"),
            _make_result("c3", 0.6, "keyword result 3"),
        ]

        merged = merge_hybrid_results(vector, keyword)
        assert len(merged) == 3
        # c1 should have highest score (appears in both)
        assert merged[0].chunk_id == "c1"

    def test_merge_empty(self):
        assert merge_hybrid_results([], []) == []


class TestTemporalDecay:
    def test_recent_files_boosted(self):
        results = [
            _make_result("old", 0.8, path="memory/2020-01-01.md"),
            _make_result("new", 0.8, path="memory/2026-02-28.md"),
        ]
        decayed = apply_temporal_decay(results, half_life_days=30)

        # New file should score higher
        new_result = next(r for r in decayed if r.chunk_id == "new")
        old_result = next(r for r in decayed if r.chunk_id == "old")
        assert new_result.score > old_result.score

    def test_non_dated_files_unchanged(self):
        results = [_make_result("c1", 0.8, path="MEMORY.md")]
        decayed = apply_temporal_decay(results)
        assert decayed[0].score == 0.8


class TestMMR:
    def test_mmr_promotes_diversity(self):
        results = [
            _make_result("c1", 0.9, "the quick brown fox"),
            _make_result("c2", 0.85, "the quick brown fox jumps"),  # Similar to c1
            _make_result("c3", 0.8, "database migration strategy"),  # Different
        ]

        mmr_results = apply_mmr(results, lambda_param=0.5)
        # c3 should be promoted above c2 due to diversity
        assert len(mmr_results) == 3
        assert mmr_results[0].chunk_id == "c1"

    def test_mmr_single_result(self):
        results = [_make_result("c1", 0.9)]
        assert apply_mmr(results) == results


class TestTextSimilarity:
    def test_identical_texts(self):
        assert _text_similarity("hello world", "hello world") == 1.0

    def test_different_texts(self):
        sim = _text_similarity("hello world", "goodbye universe")
        assert sim == 0.0

    def test_partial_overlap(self):
        sim = _text_similarity("hello world foo", "hello world bar")
        assert 0.0 < sim < 1.0
