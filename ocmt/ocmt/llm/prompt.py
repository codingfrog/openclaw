"""System prompt construction.

Inspired by OpenClaw's src/agents/cli-runner/helpers.ts buildSystemPrompt()
and src/agents/system-prompt.ts. Builds the system prompt that gets injected
into Claude Code CLI via --append-system-prompt.
"""

from __future__ import annotations

import datetime


def build_system_prompt(
    tenant_id: str,
    tenant_name: str | None = None,
    agent_id: str = "main",
    memory_context: str | None = None,
    extra: str | None = None,
) -> str:
    """Build the system prompt for a tenant's agent run.

    Includes:
    - Tenant identity
    - Current date/time
    - Memory context (today + yesterday daily logs, MEMORY.md)
    - Any extra instructions
    """
    parts: list[str] = []

    # Identity
    display_name = tenant_name or tenant_id
    parts.append(f"You are an AI assistant for {display_name}.")

    # Current time (from OpenClaw's resolveCronStyleNow pattern)
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    parts.append(f"Current time: {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    # Memory context
    if memory_context:
        parts.append("")
        parts.append("## Memory Context")
        parts.append(
            "Below is your memory from recent sessions. "
            "Use this to maintain continuity and recall prior decisions."
        )
        parts.append("")
        parts.append(memory_context)

    # Memory instructions
    parts.append("")
    parts.append("## Memory Instructions")
    parts.append(
        "When you learn important facts, preferences, or make decisions, "
        "record them in the memory files:"
    )
    parts.append(
        f"- Daily log: memory/{now.strftime('%Y-%m-%d')}.md (append-only)"
    )
    parts.append("- Long-term: MEMORY.md (curated, important facts)")

    if extra:
        parts.append("")
        parts.append(extra)

    return "\n".join(parts)


def build_memory_context(
    daily_today: str | None = None,
    daily_yesterday: str | None = None,
    long_term: str | None = None,
) -> str | None:
    """Combine memory files into context for the system prompt.

    Mirrors OpenClaw's bootstrap-files.ts pattern of loading
    today + yesterday daily logs + MEMORY.md at session start.
    """
    parts: list[str] = []

    if long_term and long_term.strip():
        parts.append("### MEMORY.md (long-term)")
        parts.append(long_term.strip())

    if daily_yesterday and daily_yesterday.strip():
        parts.append("")
        parts.append("### Yesterday's Log")
        parts.append(daily_yesterday.strip())

    if daily_today and daily_today.strip():
        parts.append("")
        parts.append("### Today's Log")
        parts.append(daily_today.strip())

    if not parts:
        return None

    return "\n".join(parts)
