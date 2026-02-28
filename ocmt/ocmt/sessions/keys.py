"""Session key derivation and parsing.

Ported from OpenClaw's src/routing/session-key.ts.
Session keys encode the agent, channel, and user context
for routing and isolation.

Format: agent:{agentId}:{channel}:user-{userId}
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ParsedSessionKey:
    """Parsed components of a session key."""

    agent_id: str
    channel: str
    rest: str


def build_session_key(
    agent_id: str = "main",
    channel: str = "api",
    user_id: str = "",
) -> str:
    """Build a session key from components.

    The tenant_id is NOT embedded in the session key — tenant isolation
    is at the auth layer. Session keys are scoped within a tenant.
    """
    parts = [f"agent:{agent_id}", channel]
    if user_id:
        parts.append(f"user-{user_id}")
    return ":".join(parts)


def parse_session_key(session_key: str) -> ParsedSessionKey | None:
    """Parse a session key into components.

    Mirrors OpenClaw's parseAgentSessionKey from session-key.ts.
    """
    raw = session_key.strip().lower()
    if not raw:
        return None

    parts = [p for p in raw.split(":") if p]
    if len(parts) < 3 or parts[0] != "agent":
        return None

    agent_id = parts[1]
    channel = parts[2]
    rest = ":".join(parts[2:])

    return ParsedSessionKey(agent_id=agent_id, channel=channel, rest=rest)


def derive_chat_type(session_key: str) -> str:
    """Determine if a session is direct, group, or channel.

    Mirrors OpenClaw's deriveChatTypeFromSessionKey.
    """
    parsed = parse_session_key(session_key)
    if not parsed:
        return "unknown"

    tokens = set(parsed.rest.lower().split(":"))
    if "group" in tokens:
        return "group"
    if "channel" in tokens:
        return "channel"
    return "direct"
