"""Agent orchestrator: the top-level execution loop.

Ties together LLM execution, memory, sessions, and tenant isolation.
Mirrors OpenClaw's src/auto-reply/reply/agent-runner.ts +
agent-runner-execution.ts flow:

    Inbound message
      -> Resolve tenant (from API key)
      -> Resolve session (from tenant + channel + user)
      -> Load memory context (today + yesterday + MEMORY.md)
      -> Check if memory flush needed
      -> Build system prompt with context
      -> Execute via CLI runner (with model fallback)
      -> Parse response, update session state
      -> Return result
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from ..config import AppConfig
from ..llm.fallback import run_with_fallback
from ..llm.prompt import build_memory_context, build_system_prompt
from ..llm.runner import run_cli, run_cli_streaming
from ..llm.types import LLMRequest, LLMResponse, LLMUsage
from ..memory.flush import build_flush_prompt, should_run_memory_flush
from ..memory.indexer import MemoryIndexer
from ..memory.search import MemorySearch
from ..memory.store import load_bootstrap_memory
from ..sessions.compaction import compact_session, needs_compaction
from ..sessions.keys import build_session_key
from ..sessions.store import SessionStore
from ..sessions.types import SessionEntry
from ..tenants.manager import TenantManager
from ..tenants.types import TenantWorkspace

logger = logging.getLogger(__name__)


@dataclass
class AgentRunResult:
    """Result from an agent run."""

    text: str
    session_key: str
    cli_session_id: str | None = None
    usage: LLMUsage | None = None
    model: str | None = None


class AgentRunner:
    """Top-level agent orchestrator.

    Each call to run() executes a complete agent turn:
    memory context loading, optional flush, LLM execution, and state update.
    """

    def __init__(
        self,
        config: AppConfig,
        tenant_manager: TenantManager,
        session_store: SessionStore,
    ) -> None:
        self.config = config
        self.tenants = tenant_manager
        self.sessions = session_store
        self._indexers: dict[str, MemoryIndexer] = {}

    async def run(
        self,
        tenant_id: str,
        prompt: str,
        user_id: str = "",
        agent_id: str = "main",
        channel: str = "api",
        model: str | None = None,
    ) -> AgentRunResult:
        """Execute a full agent turn.

        This is the main entry point — equivalent to OpenClaw's
        runReplyAgent + runAgentTurnWithFallback.
        """
        workspace = self.tenants.resolve_workspace(tenant_id)
        tenant = self.tenants.get_tenant(tenant_id)

        # 1. Resolve or create session
        session_key = build_session_key(agent_id, channel, user_id)
        session = self.sessions.get_or_create(
            tenant_id=tenant_id,
            session_key=session_key,
            agent_id=agent_id,
            channel=channel,
        )

        # 2. Check for compaction
        if needs_compaction(session):
            logger.info("Session needs compaction: %s", session_key)
            session = compact_session(session, workspace, self.sessions)

        # 3. Check memory flush
        if should_run_memory_flush(
            total_tokens=session.total_tokens,
            compaction_count=session.compaction_count,
            last_flush_at=session.memory_flush_compaction_count,
        ):
            await self._run_memory_flush(
                tenant_id, workspace, session,
                api_key=tenant.anthropic_api_key or None,
            )

        # 4. Load memory context
        memory_files = load_bootstrap_memory(workspace)
        memory_context = build_memory_context(**memory_files)

        # 5. Build system prompt
        system_prompt = build_system_prompt(
            tenant_id=tenant_id,
            tenant_name=tenant.name,
            agent_id=agent_id,
            memory_context=memory_context,
        )

        # 6. Execute with fallback chain
        resolved_model = model or self.config.agents.default_model
        models = [resolved_model] + [
            m for m in self.config.agents.fallback_models
            if m != resolved_model
        ]

        # Per-tenant API key takes priority, then falls back to global config.
        tenant_api_key = tenant.anthropic_api_key or None

        response = await run_with_fallback(
            models=models,
            runner=lambda m: run_cli(
                request=LLMRequest(
                    prompt=prompt,
                    system_prompt=system_prompt,
                    model=m,
                    session_id=session.cli_session_id,
                    resume=bool(session.cli_session_id),
                    workspace_dir=str(workspace.root / "workspace"),
                    timeout_ms=self.config.agents.cli.timeout_ms,
                ),
                tenant_id=tenant_id,
                cli_config=self.config.agents.cli,
                api_key=tenant_api_key,
            ),
        )

        # 7. Record transcript
        SessionStore.append_transcript(workspace, session_key, "user", prompt)
        SessionStore.append_transcript(workspace, session_key, "assistant", response.text)

        # 8. Update session state
        total_tokens = (response.usage.total if response.usage else 0) + session.total_tokens
        self.sessions.update(
            tenant_id=tenant_id,
            session_key=session_key,
            cli_session_id=response.session_id,
            total_tokens=total_tokens,
        )

        # 9. Mark memory dirty (response may have written to memory files)
        indexer = self._get_indexer(tenant_id, workspace)
        indexer.mark_dirty()

        return AgentRunResult(
            text=response.text,
            session_key=session_key,
            cli_session_id=response.session_id,
            usage=response.usage,
            model=resolved_model,
        )

    async def search_memory(
        self,
        tenant_id: str,
        query: str,
        max_results: int | None = None,
    ) -> list[dict]:
        """Search a tenant's memory."""
        workspace = self.tenants.resolve_workspace(tenant_id)
        indexer = self._get_indexer(tenant_id, workspace)
        search = MemorySearch(indexer, self.config.memory)

        results = search.search(query, max_results=max_results)
        return [
            {
                "path": r.path,
                "start_line": r.start_line,
                "end_line": r.end_line,
                "score": r.score,
                "snippet": r.snippet,
            }
            for r in results
        ]

    async def _run_memory_flush(
        self,
        tenant_id: str,
        workspace: TenantWorkspace,
        session: SessionEntry,
        api_key: str | None = None,
    ) -> None:
        """Run a silent memory flush turn.

        Asks the LLM to write durable memories before compaction.
        Mirrors OpenClaw's runMemoryFlushIfNeeded from agent-runner-memory.ts.
        """
        logger.info("Running memory flush for session: %s", session.session_key)

        flush_prompt = build_flush_prompt()
        try:
            await run_cli(
                request=LLMRequest(
                    prompt=flush_prompt,
                    model=self.config.agents.default_model,
                    session_id=session.cli_session_id,
                    resume=bool(session.cli_session_id),
                    workspace_dir=str(workspace.root / "workspace"),
                    timeout_ms=self.config.agents.cli.timeout_ms,
                ),
                tenant_id=tenant_id,
                cli_config=self.config.agents.cli,
                api_key=api_key,
            )

            self.sessions.update(
                tenant_id=tenant_id,
                session_key=session.session_key,
                memory_flush_compaction_count=session.compaction_count,
            )
        except Exception:
            logger.exception("Memory flush failed for %s", session.session_key)

    def _get_indexer(
        self,
        tenant_id: str,
        workspace: TenantWorkspace,
    ) -> MemoryIndexer:
        """Get or create a cached memory indexer for a tenant."""
        if tenant_id not in self._indexers:
            self._indexers[tenant_id] = MemoryIndexer(workspace, self.config.memory)
        return self._indexers[tenant_id]
