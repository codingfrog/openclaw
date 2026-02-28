"""Pre-compaction memory flush.

Ported from OpenClaw's src/auto-reply/reply/memory-flush.ts.
Before a session's context is compacted (old turns discarded),
this triggers a silent LLM turn asking the agent to write
durable memories to disk.
"""

from __future__ import annotations

import datetime

# Default memory flush prompt (from OpenClaw's DEFAULT_MEMORY_FLUSH_PROMPT)
DEFAULT_FLUSH_PROMPT = (
    "Pre-compaction memory flush. "
    "Store durable memories now (use memory/{date}.md; create memory/ if needed). "
    "IMPORTANT: If the file already exists, APPEND new content only and "
    "do not overwrite existing entries. "
    "If nothing to store, reply with NO_REPLY."
)

DEFAULT_FLUSH_SYSTEM_PROMPT = (
    "Pre-compaction memory flush turn. "
    "The session is near auto-compaction; capture durable memories to disk. "
    "You may reply, but usually NO_REPLY is correct."
)


def should_run_memory_flush(
    total_tokens: int,
    context_window: int = 200_000,
    reserve_floor: int = 8_000,
    soft_threshold: int = 4_000,
    compaction_count: int = 0,
    last_flush_at: int | None = None,
) -> bool:
    """Decide whether to run a memory flush before compaction.

    Mirrors OpenClaw's shouldRunMemoryFlush from memory-flush.ts.

    Returns True if:
    - Token count exceeds (context_window - reserve_floor - soft_threshold)
    - Haven't flushed at this compaction count yet
    """
    if total_tokens <= 0:
        return False

    threshold = max(0, context_window - reserve_floor - soft_threshold)
    if threshold <= 0:
        return False

    if total_tokens < threshold:
        return False

    # Only flush once per compaction cycle
    if last_flush_at is not None and last_flush_at == compaction_count:
        return False

    return True


def build_flush_prompt(date: datetime.date | None = None) -> str:
    """Build the memory flush prompt with today's date."""
    d = date or datetime.date.today()
    return DEFAULT_FLUSH_PROMPT.replace("{date}", d.isoformat())
