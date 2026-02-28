"""API authentication middleware.

Resolves API key to tenant. Keys are verified via bcrypt hash
lookup against the global database.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..tenants.manager import TenantManager
from ..tenants.types import Tenant

_bearer_scheme = HTTPBearer()

# Global reference set by app startup
_tenant_manager: TenantManager | None = None


def set_tenant_manager(manager: TenantManager) -> None:
    """Set the global tenant manager for auth middleware."""
    global _tenant_manager
    _tenant_manager = manager


async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> Tenant:
    """FastAPI dependency: authenticate API key and return tenant.

    Usage:
        @router.get("/api/v1/something")
        async def handler(tenant: Tenant = Depends(get_current_tenant)):
            ...
    """
    if _tenant_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant manager not initialized",
        )

    tenant = _tenant_manager.authenticate(credentials.credentials)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )

    return tenant
