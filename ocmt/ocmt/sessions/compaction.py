"""Session compaction: context window management.

When a session approaches the context window limit, compaction:
1. Triggers memory flush (persist important context to markdown)
2. Starts a fresh CLI session with condensed context
3. Increments the compaction counter

Ported from OpenClaw's src/agents/compaction.ts concepts.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..tenants.types import TenantWorkspace
from .store import SessionStore
from .types import SessionEntry


def needs_compaction(
    session: SessionEntry,
    context_window: int = 200_000,
    reserve_floor: int = 8_000,
) -> bool:
    """Check if a session needs compaction.

    Returns True when total_tokens exceeds
    (context_window - reserve_floor).
    """
    threshold = max(0, context_window - reserve_floor)
    return session.total_tokens >= threshold


def compact_session(
    session: SessionEntry,
    workspace: TenantWorkspace,
    store: SessionStore,
) -> SessionEntry:
    """Compact a session by resetting the CLI session.

    This discards the old Claude Code CLI session and starts fresh.
    The transcript is preserved for audit but a new CLI session
    will be created on the next run.

    Returns the updated session entry.
    """
    # Increment compaction count
    new_count = session.compaction_count + 1

    store.update(
        tenant_id=session.tenant_id,
        session_key=session.session_key,
        cli_session_id=None,  # Reset — next run creates a new CLI session
        total_tokens=0,
        compaction_count=new_count,
    )

    return SessionEntry(
        tenant_id=session.tenant_id,
        session_key=session.session_key,
        agent_id=session.agent_id,
        cli_session_id=None,
        channel=session.channel,
        total_tokens=0,
        compaction_count=new_count,
        memory_flush_compaction_count=session.memory_flush_compaction_count,
        last_active=session.last_active,
        created_at=session.created_at,
    )
