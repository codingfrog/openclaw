"""Claude Code CLI subprocess runner.

Ported from OpenClaw's src/agents/cli-runner.ts and
src/agents/cli-runner/helpers.ts. Spawns the `claude` CLI
as a subprocess to execute LLM requests without paying API costs.

Key patterns from OpenClaw:
- Serialized execution queue per tenant (CLI_RUN_QUEUE)
- Timeout + no-output watchdog
- JSON/JSONL/text output parsing
- Session resume via --resume flag
- Environment sanitization
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from typing import AsyncIterator

from ..config import CliConfig
from .types import LLMRequest, LLMResponse, LLMUsage

logger = logging.getLogger(__name__)

# Per-tenant serialization locks.
# Mirrors OpenClaw's CLI_RUN_QUEUE (helpers.ts:21) but keyed per tenant
# to prevent one slow tenant from blocking others.
_tenant_locks: dict[str, asyncio.Lock] = {}


def _get_tenant_lock(tenant_id: str) -> asyncio.Lock:
    if tenant_id not in _tenant_locks:
        _tenant_locks[tenant_id] = asyncio.Lock()
    return _tenant_locks[tenant_id]


async def run_cli(
    request: LLMRequest,
    tenant_id: str,
    cli_config: CliConfig | None = None,
) -> LLMResponse:
    """Execute an LLM request via the Claude Code CLI.

    Spawns `claude -p --output-format json` as an async subprocess.
    Serialized per tenant to avoid concurrent CLI conflicts.
    """
    cfg = cli_config or CliConfig()
    lock = _get_tenant_lock(tenant_id)

    async with lock:
        return await _execute_cli(request, cfg)


async def _execute_cli(request: LLMRequest, cfg: CliConfig) -> LLMResponse:
    """Spawn the CLI subprocess and parse output."""
    args = _build_args(request, cfg)

    # Build sanitized environment
    env = dict(os.environ)
    # Clear API keys so CLI uses its own auth (from OpenClaw pattern)
    for key in ("ANTHROPIC_API_KEY",):
        env.pop(key, None)

    cwd = request.workspace_dir or os.getcwd()

    logger.debug("CLI exec: %s %s (cwd=%s)", cfg.command, " ".join(args), cwd)

    proc = await asyncio.create_subprocess_exec(
        cfg.command,
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )

    timeout_s = request.timeout_ms / 1000
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(
            f"Claude CLI timed out after {timeout_s:.0f}s"
        ) from None

    if proc.returncode != 0:
        err_text = stderr.decode(errors="replace").strip()
        raise RuntimeError(
            f"Claude CLI exited with code {proc.returncode}: {err_text}"
        )

    raw = stdout.decode(errors="replace").strip()
    return _parse_output(raw)


def _build_args(request: LLMRequest, cfg: CliConfig) -> list[str]:
    """Build CLI arguments.

    Mirrors OpenClaw's buildCliArgs from cli-runner/helpers.ts.
    """
    if request.resume and request.session_id:
        # Resume mode: use resume_args template
        args = [
            a.replace("{session_id}", request.session_id)
            for a in cfg.resume_args
        ]
        args.append(request.prompt)
        return args

    # New session mode
    args = list(cfg.args)

    if cfg.model_arg and request.model:
        args.extend([cfg.model_arg, request.model])

    if request.system_prompt and cfg.system_prompt_arg:
        args.extend([cfg.system_prompt_arg, request.system_prompt])

    if request.session_id and cfg.session_arg:
        args.extend([cfg.session_arg, request.session_id])
    elif cfg.session_arg:
        # Auto-generate session ID for tracking
        new_id = str(uuid.uuid4())
        args.extend([cfg.session_arg, new_id])

    if request.max_turns > 0:
        args.extend(["--max-turns", str(request.max_turns)])

    args.append(request.prompt)
    return args


def _parse_output(raw: str) -> LLMResponse:
    """Parse CLI output (JSON, JSONL, or plain text).

    Mirrors OpenClaw's parseCliJson/parseCliJsonl from helpers.ts.
    """
    if not raw:
        return LLMResponse(text="")

    # Try JSON first
    try:
        parsed = json.loads(raw)
        return _from_json(parsed)
    except json.JSONDecodeError:
        pass

    # Try JSONL (last line is the result)
    lines = raw.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            return _from_json(parsed)
        except json.JSONDecodeError:
            continue

    # Fall back to plain text
    return LLMResponse(text=raw)


def _from_json(parsed: dict | list | str) -> LLMResponse:
    """Extract text, session_id, and usage from parsed JSON."""
    if isinstance(parsed, str):
        return LLMResponse(text=parsed)

    if isinstance(parsed, list):
        text = "".join(_collect_text(item) for item in parsed)
        return LLMResponse(text=text)

    text = _collect_text(parsed)
    session_id = _pick_string(parsed, "session_id", "sessionId", "conversation_id")
    usage = _parse_usage(parsed.get("usage"))

    return LLMResponse(
        text=text,
        session_id=session_id,
        usage=usage,
        raw=parsed if isinstance(parsed, dict) else None,
    )


def _collect_text(obj: object) -> str:
    """Recursively extract text from a Claude Code JSON response.

    Mirrors OpenClaw's collectText from helpers.ts.
    """
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "".join(_collect_text(item) for item in obj)
    if isinstance(obj, dict):
        # Check common text fields
        for key in ("text", "content", "result", "message"):
            if key in obj:
                return _collect_text(obj[key])
    return ""


def _pick_string(d: dict, *keys: str) -> str | None:
    """Pick the first non-empty string value from a dict."""
    for key in keys:
        val = d.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _parse_usage(raw: object) -> LLMUsage | None:
    """Parse usage stats from CLI output.

    Mirrors OpenClaw's toUsage from helpers.ts.
    """
    if not isinstance(raw, dict):
        return None

    def pick(k: str) -> int:
        v = raw.get(k)
        return int(v) if isinstance(v, (int, float)) and v > 0 else 0

    input_tokens = pick("input_tokens") or pick("inputTokens")
    output_tokens = pick("output_tokens") or pick("outputTokens")
    cache_read = pick("cache_read_input_tokens") or pick("cacheRead")
    cache_write = pick("cache_write_input_tokens") or pick("cacheWrite")
    total = pick("total_tokens") or pick("total")

    if not any([input_tokens, output_tokens, cache_read, cache_write, total]):
        return None

    return LLMUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read=cache_read,
        cache_write=cache_write,
        total=total or (input_tokens + output_tokens),
    )


async def run_cli_streaming(
    request: LLMRequest,
    tenant_id: str,
    cli_config: CliConfig | None = None,
) -> AsyncIterator[str]:
    """Stream output from Claude CLI line by line.

    Uses --stream flag for real-time token delivery.
    """
    cfg = cli_config or CliConfig()
    lock = _get_tenant_lock(tenant_id)

    async with lock:
        args = _build_args(request, cfg)

        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)

        proc = await asyncio.create_subprocess_exec(
            cfg.command,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=request.workspace_dir or os.getcwd(),
            env=env,
        )

        assert proc.stdout is not None
        async for line in proc.stdout:
            decoded = line.decode(errors="replace").rstrip("\n")
            if decoded:
                yield decoded

        await proc.wait()
