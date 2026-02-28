"""OCMT entry point: FastAPI app + CLI bootstrap.

Two entry modes:
1. API server: `ocmt serve` or `python -m ocmt serve`
2. CLI: `ocmt chat -t <tenant> "message"`
"""

from __future__ import annotations

from fastapi import FastAPI

from .config import AppConfig, load_config


def create_app(config_path: str = "config.yaml") -> FastAPI:
    """Create and configure the FastAPI application."""
    config = load_config(config_path)
    app = FastAPI(title="OCMT", version="0.1.0")

    @app.on_event("startup")
    async def startup():
        from .agents.runner import AgentRunner
        from .api.auth import set_tenant_manager
        from .api.routes import router, set_dependencies
        from .api.websocket import chat_websocket, set_ws_dependencies
        from .sessions.store import SessionStore
        from .tenants.manager import TenantManager

        tenant_mgr = TenantManager(config.tenant_workspace_root, config.database.path)
        session_store = SessionStore(tenant_mgr.db)
        runner = AgentRunner(config, tenant_mgr, session_store)

        # Wire up dependencies
        set_tenant_manager(tenant_mgr)
        set_dependencies(runner, tenant_mgr, session_store)
        set_ws_dependencies(runner, tenant_mgr)

        # Store on app state for cleanup
        app.state.tenant_manager = tenant_mgr
        app.state.agent_runner = runner

        # Register routes
        app.include_router(router)
        app.add_api_websocket_route("/ws/chat", chat_websocket)

    @app.on_event("shutdown")
    async def shutdown():
        if hasattr(app.state, "tenant_manager"):
            app.state.tenant_manager.close()

    return app


# CLI entry point
def cli_app():
    """CLI entry point via `ocmt` command."""
    from .cli.commands import app as typer_app
    typer_app()


if __name__ == "__main__":
    cli_app()
