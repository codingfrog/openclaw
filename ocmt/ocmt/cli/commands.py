"""CLI commands using Typer.

Provides commands for:
- chat: Send a message to an agent
- tenant: Create/list tenants
- memory: Search/status
- serve: Start the API server
"""

from __future__ import annotations

import asyncio
import json
import sys

import typer

app = typer.Typer(name="ocmt", help="Multi-tenant LLM with Markdown memory")


def _get_app_objects(config_path: str = "config.yaml"):
    """Lazy-init shared objects."""
    from ..config import load_config
    from ..agents.runner import AgentRunner
    from ..sessions.store import SessionStore
    from ..tenants.manager import TenantManager

    config = load_config(config_path)
    tenant_mgr = TenantManager(config.tenant_workspace_root, config.database.path)
    session_store = SessionStore(tenant_mgr.db)
    runner = AgentRunner(config, tenant_mgr, session_store)
    return config, tenant_mgr, session_store, runner


# --- Chat ---


@app.command()
def chat(
    message: str = typer.Argument(..., help="Message to send"),
    tenant: str = typer.Option(..., "--tenant", "-t", help="Tenant slug"),
    user_id: str = typer.Option("cli-user", "--user", "-u"),
    model: str | None = typer.Option(None, "--model", "-m"),
    config_path: str = typer.Option("config.yaml", "--config", "-c"),
):
    """Send a message and get a response."""
    config, tenant_mgr, session_store, runner = _get_app_objects(config_path)

    tenant_obj = tenant_mgr.get_tenant_by_slug(tenant)
    if not tenant_obj:
        typer.echo(f"Tenant not found: {tenant}", err=True)
        raise typer.Exit(1)

    async def _run():
        return await runner.run(
            tenant_id=tenant_obj.id,
            prompt=message,
            user_id=user_id,
            channel="cli",
            model=model,
        )

    result = asyncio.run(_run())
    typer.echo(result.text)


# --- Tenant ---


@app.command("tenant-create")
def tenant_create(
    name: str = typer.Argument(..., help="Tenant display name"),
    slug: str = typer.Argument(..., help="URL-safe identifier"),
    anthropic_api_key: str = typer.Option(
        "", "--anthropic-key", help="Per-tenant Anthropic API key for billing"
    ),
    config_path: str = typer.Option("config.yaml", "--config", "-c"),
):
    """Create a new tenant."""
    config, tenant_mgr, _, _ = _get_app_objects(config_path)

    existing = tenant_mgr.get_tenant_by_slug(slug)
    if existing:
        typer.echo(f"Tenant already exists: {slug}", err=True)
        raise typer.Exit(1)

    tenant, api_key = tenant_mgr.create_tenant(
        name, slug, anthropic_api_key=anthropic_api_key
    )
    typer.echo(f"Created tenant: {tenant.name} ({tenant.slug})")
    typer.echo(f"Tenant ID: {tenant.id}")
    typer.echo(f"API Key: {api_key}")
    typer.echo("Save this API key — it won't be shown again.")


@app.command("tenant-list")
def tenant_list(
    config_path: str = typer.Option("config.yaml", "--config", "-c"),
):
    """List all tenants."""
    config, tenant_mgr, _, _ = _get_app_objects(config_path)
    tenants = tenant_mgr.list_tenants()

    if not tenants:
        typer.echo("No tenants found.")
        return

    for t in tenants:
        typer.echo(f"  {t.slug} — {t.name} (id: {t.id})")


# --- Memory ---


@app.command("memory-search")
def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    tenant: str = typer.Option(..., "--tenant", "-t"),
    max_results: int = typer.Option(6, "--max", "-n"),
    config_path: str = typer.Option("config.yaml", "--config", "-c"),
):
    """Search a tenant's memory."""
    config, tenant_mgr, session_store, runner = _get_app_objects(config_path)

    tenant_obj = tenant_mgr.get_tenant_by_slug(tenant)
    if not tenant_obj:
        typer.echo(f"Tenant not found: {tenant}", err=True)
        raise typer.Exit(1)

    async def _run():
        return await runner.search_memory(
            tenant_id=tenant_obj.id,
            query=query,
            max_results=max_results,
        )

    results = asyncio.run(_run())

    if not results:
        typer.echo("No results found.")
        return

    for r in results:
        typer.echo(f"\n--- {r['path']}:{r['start_line']}-{r['end_line']} (score: {r['score']:.3f})")
        typer.echo(r["snippet"][:200])


@app.command("memory-status")
def memory_status(
    tenant: str = typer.Option(..., "--tenant", "-t"),
    config_path: str = typer.Option("config.yaml", "--config", "-c"),
):
    """Show memory index status for a tenant."""
    from ..memory.indexer import MemoryIndexer

    config, tenant_mgr, _, _ = _get_app_objects(config_path)

    tenant_obj = tenant_mgr.get_tenant_by_slug(tenant)
    if not tenant_obj:
        typer.echo(f"Tenant not found: {tenant}", err=True)
        raise typer.Exit(1)

    workspace = tenant_mgr.resolve_workspace(tenant_obj.id)
    indexer = MemoryIndexer(workspace, config.memory)

    status = indexer.status()
    typer.echo(json.dumps(status, indent=2))
    indexer.close()


# --- Server ---


@app.command("serve")
def serve(
    config_path: str = typer.Option("config.yaml", "--config", "-c"),
    host: str | None = typer.Option(None, "--host"),
    port: int | None = typer.Option(None, "--port"),
):
    """Start the API server."""
    import uvicorn

    from ..config import load_config

    config = load_config(config_path)
    resolved_host = host or config.api.host
    resolved_port = port or config.api.port

    # Import here to avoid circular imports
    from ..main import create_app

    app_instance = create_app(config_path)

    typer.echo(f"Starting OCMT server on {resolved_host}:{resolved_port}")
    uvicorn.run(app_instance, host=resolved_host, port=resolved_port)
