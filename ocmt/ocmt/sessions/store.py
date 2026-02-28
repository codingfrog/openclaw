"""Session store: metadata persistence and JSONL transcripts.

Ported from OpenClaw's src/config/sessions/store.ts.
Sessions are stored in:
- Global SQLite: metadata (token counts, compaction state)
- JSONL files: conversation transcripts per session
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from ..tenants.types import TenantWorkspace
from .types import SessionEntry


class SessionStore:
    """Manage session lifecycle and transcripts.

    Uses the global SQLite for metadata and per-tenant JSONL files
    for conversation transcripts.
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.db.row_factory = sqlite3.Row

    def get_or_create(
        self,
        tenant_id: str,
        session_key: str,
        agent_id: str = "main",
        channel: str | None = None,
    ) -> SessionEntry:
        """Get an existing session or create a new one."""
        row = self.db.execute(
            "SELECT * FROM sessions WHERE tenant_id = ? AND session_key = ?",
            (tenant_id, session_key),
        ).fetchone()

        if row:
            return SessionEntry(**dict(row))

        now = time.time()
        entry = SessionEntry(
            tenant_id=tenant_id,
            session_key=session_key,
            agent_id=agent_id,
            channel=channel,
            last_active=now,
            created_at=now,
        )

        self.db.execute(
            """INSERT INTO sessions
               (tenant_id, session_key, agent_id, channel, total_tokens,
                compaction_count, memory_flush_compaction_count, last_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tenant_id, session_key, agent_id, channel, 0, 0, 0, now, now),
        )
        self.db.commit()
        return entry

    def update(
        self,
        tenant_id: str,
        session_key: str,
        cli_session_id: str | None = None,
        total_tokens: int | None = None,
        compaction_count: int | None = None,
        memory_flush_compaction_count: int | None = None,
    ) -> None:
        """Update session metadata after an agent run."""
        updates: list[str] = ["last_active = ?"]
        params: list[object] = [time.time()]

        if cli_session_id is not None:
            updates.append("cli_session_id = ?")
            params.append(cli_session_id)
        if total_tokens is not None:
            updates.append("total_tokens = ?")
            params.append(total_tokens)
        if compaction_count is not None:
            updates.append("compaction_count = ?")
            params.append(compaction_count)
        if memory_flush_compaction_count is not None:
            updates.append("memory_flush_compaction_count = ?")
            params.append(memory_flush_compaction_count)

        params.extend([tenant_id, session_key])

        self.db.execute(
            f"UPDATE sessions SET {', '.join(updates)} "
            f"WHERE tenant_id = ? AND session_key = ?",
            params,
        )
        self.db.commit()

    def list_sessions(self, tenant_id: str) -> list[SessionEntry]:
        """List all sessions for a tenant."""
        rows = self.db.execute(
            "SELECT * FROM sessions WHERE tenant_id = ? ORDER BY last_active DESC",
            (tenant_id,),
        ).fetchall()
        return [SessionEntry(**dict(row)) for row in rows]

    def delete_session(self, tenant_id: str, session_key: str) -> None:
        """Delete a session."""
        self.db.execute(
            "DELETE FROM sessions WHERE tenant_id = ? AND session_key = ?",
            (tenant_id, session_key),
        )
        self.db.commit()

    # --- JSONL Transcript I/O ---

    @staticmethod
    def transcript_path(workspace: TenantWorkspace, session_key: str) -> Path:
        """Get the JSONL transcript file path for a session."""
        # Sanitize session key for filesystem
        safe_key = session_key.replace(":", "_").replace("/", "_")
        return workspace.sessions_dir / f"{safe_key}.jsonl"

    @staticmethod
    def append_transcript(
        workspace: TenantWorkspace,
        session_key: str,
        role: str,
        content: str,
    ) -> None:
        """Append a message turn to the session transcript."""
        path = SessionStore.transcript_path(workspace, session_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }

        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    @staticmethod
    def read_transcript(
        workspace: TenantWorkspace,
        session_key: str,
        last_n: int | None = None,
    ) -> list[dict]:
        """Read the session transcript. Optionally return only the last N turns."""
        path = SessionStore.transcript_path(workspace, session_key)
        if not path.exists():
            return []

        entries: list[dict] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        if last_n is not None:
            return entries[-last_n:]
        return entries
