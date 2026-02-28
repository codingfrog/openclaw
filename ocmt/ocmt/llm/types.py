"""LLM request/response types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMUsage:
    """Token usage statistics from an LLM run."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total: int = 0


@dataclass
class LLMResponse:
    """Response from an LLM execution.

    Mirrors OpenClaw's CliOutput from src/agents/cli-runner/helpers.ts.
    """

    text: str
    session_id: str | None = None
    usage: LLMUsage | None = None
    model: str | None = None
    raw: dict[str, Any] | None = None


@dataclass
class LLMRequest:
    """Request to send to the LLM."""

    prompt: str
    system_prompt: str | None = None
    model: str = "sonnet"
    session_id: str | None = None
    resume: bool = False
    workspace_dir: str | None = None
    timeout_ms: int = 120_000
    max_turns: int = 1
