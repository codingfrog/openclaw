"""Embedding provider interface.

Abstract base for embedding providers. The system works without
embeddings (FTS-only mode). Add a provider to enable hybrid
BM25+vector search.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingProvider(ABC):
    """Abstract embedding provider."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """The model identifier."""

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts."""
