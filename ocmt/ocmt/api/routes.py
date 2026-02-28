"""FastAPI REST API routes.

Provides endpoints for:
- Chat (send message, get response)
- Memory (search, read, write)
- Sessions (list, delete)
- Tenants (create, list) — admin endpoints
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
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


# --- Request/Response Models ---


class ChatRequest(BaseModel):
    message: str
    user_id: str = ""
    agent_id: str = "main"
    channel: str = "api"
    model: str | None = None


class ChatResponse(BaseModel):
    text: str
    session_key: str
    model: str | None = None
    usage: dict | None = None


class MemorySearchRequest(BaseModel):
    query: str
    max_results: int = 6


class MemoryWriteRequest(BaseModel):
    content: str
    date: str | None = None  # YYYY-MM-DD, defaults to today


class TenantCreateRequest(BaseModel):
    name: str
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    # Per-tenant Anthropic API key for CLI billing (optional).
    # If not set, the global agents.cli.api_key / ANTHROPIC_API_KEY is used.
    anthropic_api_key: str = ""


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
    return {"path": str(path), "status": "appended"}


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


@router.post("/tenants", response_model=TenantCreateResponse)
async def create_tenant(req: TenantCreateRequest):
    """Create a new tenant. Returns the API key (shown only once)."""
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


@router.get("/tenants")
async def list_tenants():
    """List all tenants (admin)."""
    tenants = _tenant_manager.list_tenants()
    return {
        "tenants": [
            {"id": t.id, "name": t.name, "slug": t.slug}
            for t in tenants
        ]
    }
