"""Markdown text chunking for memory indexing.

Splits markdown files into overlapping chunks for indexing and search.
Defaults ported from OpenClaw: 400 tokens per chunk, 80 token overlap.

Since we don't have a tokenizer, we approximate tokens as words * 1.3.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Chunk:
    """A chunk of text from a memory file."""

    text: str
    start_line: int  # 1-based
    end_line: int  # 1-based inclusive


# Approximate token count: ~1.3 tokens per word for English text.
_TOKENS_PER_WORD = 1.3


def _estimate_tokens(text: str) -> int:
    """Estimate token count from text."""
    words = len(text.split())
    return int(words * _TOKENS_PER_WORD)


def chunk_markdown(
    content: str,
    tokens_per_chunk: int = 400,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    """Split markdown content into overlapping chunks.

    Strategy:
    1. Split on markdown headings (##, ###) as natural boundaries
    2. If a section is too long, split on paragraphs (double newline)
    3. If still too long, split on sentence boundaries
    4. Apply overlap between adjacent chunks

    Returns chunks with their line ranges (1-based).
    """
    if not content.strip():
        return []

    lines = content.split("\n")
    sections = _split_into_sections(lines)

    chunks: list[Chunk] = []
    max_words = int(tokens_per_chunk / _TOKENS_PER_WORD)
    overlap_words = int(overlap_tokens / _TOKENS_PER_WORD)

    for section_lines, start_line in sections:
        section_text = "\n".join(section_lines)
        if _estimate_tokens(section_text) <= tokens_per_chunk:
            if section_text.strip():
                chunks.append(Chunk(
                    text=section_text.strip(),
                    start_line=start_line,
                    end_line=start_line + len(section_lines) - 1,
                ))
        else:
            # Section too long — split into smaller chunks with overlap
            sub_chunks = _split_with_overlap(
                section_lines, start_line, max_words, overlap_words
            )
            chunks.extend(sub_chunks)

    return chunks


def _split_into_sections(lines: list[str]) -> list[tuple[list[str], int]]:
    """Split lines into sections based on markdown headings."""
    sections: list[tuple[list[str], int]] = []
    current_lines: list[str] = []
    current_start = 1

    for i, line in enumerate(lines):
        line_num = i + 1
        if re.match(r"^#{1,4}\s", line) and current_lines:
            sections.append((current_lines, current_start))
            current_lines = [line]
            current_start = line_num
        else:
            if not current_lines:
                current_start = line_num
            current_lines.append(line)

    if current_lines:
        sections.append((current_lines, current_start))

    return sections


def _split_with_overlap(
    lines: list[str],
    start_line: int,
    max_words: int,
    overlap_words: int,
) -> list[Chunk]:
    """Split lines into overlapping chunks by word count.

    Handles long lines by splitting them into words when a single line
    exceeds the word limit.
    """
    # Flatten lines into individual words, tracking which line each word is on.
    words: list[tuple[str, int]] = []  # (word, line_index)
    for idx, line in enumerate(lines):
        for w in line.split():
            words.append((w, idx))

    if not words:
        return []

    chunks: list[Chunk] = []
    step = max(1, max_words - overlap_words)
    pos = 0

    while pos < len(words):
        end = min(pos + max_words, len(words))
        chunk_words = words[pos:end]

        text = " ".join(w for w, _ in chunk_words)
        first_line = start_line + chunk_words[0][1]
        last_line = start_line + chunk_words[-1][1]

        if text.strip():
            chunks.append(Chunk(text=text, start_line=first_line, end_line=last_line))

        if end >= len(words):
            break
        pos += step

    return chunks
