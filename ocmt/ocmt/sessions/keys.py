"""Session key derivation and parsing.

Session keys encode the agent and user context for routing.
Channel is NOT part of the key — isolation is at the tenant level,
so the same user shares one session across all channels (CLI, API,
WebSocket, Telegram, etc.).

Format: agent:{agentId}:user-{userId}
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedSessionKey:
    """Parsed components of a session key."""

    agent_id: str
    user_id: str


def build_session_key(
    agent_id: str = "main",
    user_id: str = "",
) -> str:
    """Build a session key from agent and user.

    Channel is intentionally excluded — a user has one session per
    tenant+agent regardless of which channel they use. This ensures
    memory and conversation context are shared across CLI, API, WS,
    and chat-platform channels.

    Tenant isolation is handled at the auth layer; the tenant_id is
    NOT embedded in the session key.
    """
    parts = [f"agent:{agent_id}"]
    if user_id:
        parts.append(f"user-{user_id}")
    return ":".join(parts)


def parse_session_key(session_key: str) -> ParsedSessionKey | None:
    """Parse a session key into components."""
    raw = session_key.strip().lower()
    if not raw:
        return None

    parts = [p for p in raw.split(":") if p]
    if len(parts) < 2 or parts[0] != "agent":
        return None

    agent_id = parts[1]
    user_id = ""
    for part in parts[2:]:
        if part.startswith("user-"):
            user_id = part.removeprefix("user-")
            break

    return ParsedSessionKey(agent_id=agent_id, user_id=user_id)
