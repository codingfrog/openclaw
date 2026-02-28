"""Tenant data types."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Tenant:
    """A tenant record in the global database."""

    id: str
    name: str
    slug: str
    api_key_hash: str
    created_at: float
    config_json: str = "{}"


@dataclass
class TenantWorkspace:
    """Resolved filesystem paths for a tenant's workspace.

    Mirrors OpenClaw's per-agent workspace isolation pattern
    (src/agents/agent-scope.ts: resolveAgentWorkspaceDir) but
    scoped to a tenant boundary.
    """

    tenant_id: str
    root: Path
    memory_dir: Path = field(init=False)
    sessions_dir: Path = field(init=False)
    memory_db: Path = field(init=False)
    memory_md: Path = field(init=False)

    def __post_init__(self) -> None:
        self.memory_dir = self.root / "workspace" / "memory"
        self.sessions_dir = self.root / "sessions"
        self.memory_db = self.root / "memory.sqlite"
        self.memory_md = self.root / "workspace" / "MEMORY.md"
