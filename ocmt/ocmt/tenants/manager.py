"""Tenant lifecycle management."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from pathlib import Path

import bcrypt

from .isolation import init_workspace
from .types import Tenant, TenantWorkspace


def _ensure_global_schema(db: sqlite3.Connection) -> None:
    """Create global tables if they don't exist."""
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            api_key_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            config_json TEXT DEFAULT '{}',
            anthropic_api_key TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS sessions (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            session_key TEXT NOT NULL,
            agent_id TEXT NOT NULL DEFAULT 'main',
            cli_session_id TEXT,
            channel TEXT,
            total_tokens INTEGER DEFAULT 0,
            compaction_count INTEGER DEFAULT 0,
            memory_flush_compaction_count INTEGER DEFAULT 0,
            last_active REAL NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (tenant_id, session_key)
        );
    """)


class TenantManager:
    """Manage tenant lifecycle: create, resolve, list, delete.

    Extends OpenClaw's per-agent workspace isolation pattern
    (src/agents/agent-scope.ts) to full tenant boundaries.
    """

    def __init__(self, workspace_root: str | Path, db_path: str | Path) -> None:
        self.workspace_root = Path(workspace_root)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(db_path))
        self.db.row_factory = sqlite3.Row
        _ensure_global_schema(self.db)

    def create_tenant(
        self, name: str, slug: str, anthropic_api_key: str = ""
    ) -> tuple[Tenant, str]:
        """Create a new tenant with workspace and return (tenant, raw_api_key).

        The raw API key is returned only once at creation time.

        Args:
            anthropic_api_key: Per-tenant Anthropic API key for CLI billing.
                Per Anthropic's legal/compliance docs, multi-tenant services
                must use API key auth (not OAuth/Pro/Max credentials).
        """
        tenant_id = str(uuid.uuid4())
        raw_key = f"ocmt_{secrets.token_urlsafe(32)}"
        key_hash = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()
        now = time.time()

        self.db.execute(
            "INSERT INTO tenants (id, name, slug, api_key_hash, created_at, anthropic_api_key)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (tenant_id, name, slug, key_hash, now, anthropic_api_key),
        )
        self.db.commit()

        tenant = Tenant(
            id=tenant_id,
            name=name,
            slug=slug,
            api_key_hash=key_hash,
            created_at=now,
            anthropic_api_key=anthropic_api_key,
        )

        workspace = self.resolve_workspace(tenant_id)
        init_workspace(workspace)

        return tenant, raw_key

    def resolve_workspace(self, tenant_id: str) -> TenantWorkspace:
        """Resolve workspace paths for a tenant."""
        tenant = self.get_tenant(tenant_id)
        root = self.workspace_root / tenant.slug
        return TenantWorkspace(tenant_id=tenant_id, root=root)

    def get_tenant(self, tenant_id: str) -> Tenant:
        """Get a tenant by ID."""
        row = self.db.execute(
            "SELECT * FROM tenants WHERE id = ?", (tenant_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Tenant not found: {tenant_id}")
        return Tenant(**dict(row))

    def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """Get a tenant by slug."""
        row = self.db.execute(
            "SELECT * FROM tenants WHERE slug = ?", (slug,)
        ).fetchone()
        if not row:
            return None
        return Tenant(**dict(row))

    def authenticate(self, api_key: str) -> Tenant | None:
        """Authenticate an API key and return the matching tenant.

        Iterates all tenants and checks bcrypt hash. For production,
        consider a key prefix index for faster lookup.
        """
        rows = self.db.execute("SELECT * FROM tenants").fetchall()
        for row in rows:
            tenant = Tenant(**dict(row))
            if bcrypt.checkpw(api_key.encode(), tenant.api_key_hash.encode()):
                return tenant
        return None

    def list_tenants(self) -> list[Tenant]:
        """List all tenants."""
        rows = self.db.execute(
            "SELECT * FROM tenants ORDER BY created_at"
        ).fetchall()
        return [Tenant(**dict(row)) for row in rows]

    def delete_tenant(self, tenant_id: str) -> None:
        """Delete a tenant record. Does NOT delete workspace files."""
        self.db.execute("DELETE FROM sessions WHERE tenant_id = ?", (tenant_id,))
        self.db.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
        self.db.commit()

    def close(self) -> None:
        """Close the database connection."""
        self.db.close()
