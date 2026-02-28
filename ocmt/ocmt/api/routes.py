"""FastAPI REST API routes.

Provides endpoints for:
- Chat (send message, get response)
- Memory (search, read, write)
- Sessions (list, delete)
- Tenants (create, list) — admin endpoints
"""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from ..tenants.types import Tenant
from .auth import get_current_tenant

router = APIRouter(prefix="/api/v1")

# These get set by the app on startup
_agent_runner = None
_tenant_manager = None
_session_store = None


def set_dependencies(agent_runner, tenant_manager, session_store):
    """Set global dependencies for routes. Called during app startup."""
    global _agent_runner, _tenant_manager, _session_store
    _agent_runner = agent_runner
    _tenant_manager = tenant_manager
    _session_store = session_store


async def _require_admin_key(
    x_admin_key: str = Header(..., alias="X-Admin-Key"),
) -> None:
    """Verify the admin key for tenant management endpoints.

    The admin key is set via OCMT_ADMIN_KEY env var. If unset,
    admin endpoints are disabled (returns 403).
    """
    expected = os.environ.get("OCMT_ADMIN_KEY", "")
    if not expected:
        raise HTTPException(
            status_code=403,
            detail="Admin endpoints disabled — set OCMT_ADMIN_KEY env var",
        )
    if not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=403, detail="Invalid admin key")


# --- Request/Response Models ---


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    user_id: str = Field(default="", max_length=256)
    agent_id: str = Field(default="main", max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    channel: str = Field(default="api", max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    model: str | None = Field(default=None, max_length=64)


class ChatResponse(BaseModel):
    text: str
    session_key: str
    model: str | None = None
    usage: dict | None = None


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    max_results: int = Field(default=6, ge=1, le=50)


class MemoryWriteRequest(BaseModel):
    content: str = Field(min_length=1, max_length=500_000)
    date: str | None = None  # YYYY-MM-DD, defaults to today


class TenantCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    # Optional per-tenant Anthropic API key override.
    anthropic_api_key: str = Field(default="", max_length=256)


class TenantCreateResponse(BaseModel):
    id: str
    name: str
    slug: str
    api_key: str  # Only returned at creation time


# --- Chat ---


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Send a message and get a response from the agent."""
    result = await _agent_runner.run(
        tenant_id=tenant.id,
        prompt=req.message,
        user_id=req.user_id,
        agent_id=req.agent_id,
        channel=req.channel,
        model=req.model,
    )

    usage_dict = None
    if result.usage:
        usage_dict = {
            "input_tokens": result.usage.input_tokens,
            "output_tokens": result.usage.output_tokens,
            "total": result.usage.total,
        }

    return ChatResponse(
        text=result.text,
        session_key=result.session_key,
        model=result.model,
        usage=usage_dict,
    )


# --- Memory ---


@router.post("/memory/search")
async def memory_search(
    req: MemorySearchRequest,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Search tenant's memory."""
    results = await _agent_runner.search_memory(
        tenant_id=tenant.id,
        query=req.query,
        max_results=req.max_results,
    )
    return {"results": results}


@router.post("/memory/write")
async def memory_write(
    req: MemoryWriteRequest,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Write to tenant's daily memory log."""
    import datetime

    from ..memory.store import append_daily_log

    workspace = _tenant_manager.resolve_workspace(tenant.id)
    date = None
    if req.date:
        date = datetime.date.fromisoformat(req.date)

    path = append_daily_log(workspace, req.content, date)
    # Return relative path only — never expose absolute filesystem paths.
    try:
        rel = path.relative_to(workspace.root)
    except ValueError:
        rel = path.name
    return {"path": str(rel), "status": "appended"}


@router.get("/memory/daily/{date}")
async def memory_daily(
    date: str,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Read a daily memory log."""
    import datetime

    from ..memory.store import read_daily_log

    workspace = _tenant_manager.resolve_workspace(tenant.id)
    d = datetime.date.fromisoformat(date)
    content = read_daily_log(workspace, d)

    if content is None:
        raise HTTPException(status_code=404, detail="No log for this date")

    return {"date": date, "content": content}


# --- Sessions ---


@router.get("/sessions")
async def list_sessions(
    tenant: Tenant = Depends(get_current_tenant),
):
    """List all sessions for the tenant."""
    sessions = _session_store.list_sessions(tenant.id)
    return {
        "sessions": [
            {
                "session_key": s.session_key,
                "agent_id": s.agent_id,
                "channel": s.channel,
                "total_tokens": s.total_tokens,
                "last_active": s.last_active,
            }
            for s in sessions
        ]
    }


@router.delete("/sessions/{session_key}")
async def delete_session(
    session_key: str,
    tenant: Tenant = Depends(get_current_tenant),
):
    """Delete a session."""
    _session_store.delete_session(tenant.id, session_key)
    return {"status": "deleted"}


# --- Tenant Admin ---


@router.post(
    "/tenants",
    response_model=TenantCreateResponse,
    dependencies=[Depends(_require_admin_key)],
)
async def create_tenant(req: TenantCreateRequest):
    """Create a new tenant. Requires X-Admin-Key header."""
    existing = _tenant_manager.get_tenant_by_slug(req.slug)
    if existing:
        raise HTTPException(status_code=409, detail="Slug already exists")

    tenant, api_key = _tenant_manager.create_tenant(
        req.name, req.slug, anthropic_api_key=req.anthropic_api_key
    )
    return TenantCreateResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        api_key=api_key,
    )


@router.get("/tenants", dependencies=[Depends(_require_admin_key)])
async def list_tenants():
    """List all tenants. Requires X-Admin-Key header."""
    tenants = _tenant_manager.list_tenants()
    return {
        "tenants": [
            {"id": t.id, "name": t.name, "slug": t.slug}
            for t in tenants
        ]
    }
