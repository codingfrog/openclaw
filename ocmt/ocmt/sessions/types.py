"""Session data types.

Mirrors OpenClaw's SessionEntry from src/config/sessions/types.ts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SessionEntry:
    """A session record tracking conversation state."""

    tenant_id: str
    session_key: str
    agent_id: str = "main"
    cli_session_id: str | None = None
    channel: str | None = None
    total_tokens: int = 0
    compaction_count: int = 0
    memory_flush_compaction_count: int = 0
    last_active: float = 0.0
    created_at: float = 0.0
