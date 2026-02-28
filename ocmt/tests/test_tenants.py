"""Tests for tenant management and workspace isolation."""

import tempfile
from pathlib import Path

import pytest

from ocmt.tenants.isolation import init_workspace, validate_path_within_workspace
from ocmt.tenants.manager import TenantManager
from ocmt.tenants.types import TenantWorkspace


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def tenant_manager(tmp_dir):
    db_path = tmp_dir / "test.sqlite"
    workspace_root = tmp_dir / "tenants"
    mgr = TenantManager(str(workspace_root), str(db_path))
    yield mgr
    mgr.close()


class TestTenantManager:
    def test_create_tenant(self, tenant_manager):
        tenant, api_key = tenant_manager.create_tenant("Test Org", "test-org")
        assert tenant.name == "Test Org"
        assert tenant.slug == "test-org"
        assert api_key.startswith("ocmt_")
        assert len(api_key) > 20

    def test_authenticate(self, tenant_manager):
        tenant, api_key = tenant_manager.create_tenant("Auth Test", "auth-test")
        result = tenant_manager.authenticate(api_key)
        assert result is not None
        assert result.id == tenant.id

    def test_authenticate_wrong_key(self, tenant_manager):
        tenant_manager.create_tenant("Auth Test", "auth-test2")
        result = tenant_manager.authenticate("ocmt_wrong_key")
        assert result is None

    def test_list_tenants(self, tenant_manager):
        tenant_manager.create_tenant("Org 1", "org-1")
        tenant_manager.create_tenant("Org 2", "org-2")
        tenants = tenant_manager.list_tenants()
        assert len(tenants) == 2
        assert tenants[0].slug == "org-1"
        assert tenants[1].slug == "org-2"

    def test_get_tenant_by_slug(self, tenant_manager):
        tenant_manager.create_tenant("Slug Test", "slug-test")
        result = tenant_manager.get_tenant_by_slug("slug-test")
        assert result is not None
        assert result.name == "Slug Test"

    def test_resolve_workspace(self, tenant_manager):
        tenant, _ = tenant_manager.create_tenant("WS Test", "ws-test")
        workspace = tenant_manager.resolve_workspace(tenant.id)
        assert workspace.tenant_id == tenant.id
        assert "ws-test" in str(workspace.root)

    def test_delete_tenant(self, tenant_manager):
        tenant, _ = tenant_manager.create_tenant("Del Test", "del-test")
        tenant_manager.delete_tenant(tenant.id)
        result = tenant_manager.get_tenant_by_slug("del-test")
        assert result is None


class TestWorkspaceIsolation:
    def test_init_workspace(self, tmp_dir):
        workspace = TenantWorkspace(tenant_id="t1", root=tmp_dir / "tenant-1")
        init_workspace(workspace)
        assert workspace.memory_dir.exists()
        assert workspace.sessions_dir.exists()
        assert workspace.memory_md.exists()

    def test_validate_path_within_workspace(self, tmp_dir):
        workspace = TenantWorkspace(tenant_id="t1", root=tmp_dir / "tenant-1")
        init_workspace(workspace)

        # Valid path
        valid = workspace.memory_dir / "2026-02-28.md"
        assert validate_path_within_workspace(workspace, valid) is True

        # Path traversal attack
        evil = workspace.root / ".." / ".." / "etc" / "passwd"
        assert validate_path_within_workspace(workspace, evil) is False

    def test_tenants_have_isolated_workspaces(self, tenant_manager):
        t1, _ = tenant_manager.create_tenant("Tenant 1", "t1")
        t2, _ = tenant_manager.create_tenant("Tenant 2", "t2")

        ws1 = tenant_manager.resolve_workspace(t1.id)
        ws2 = tenant_manager.resolve_workspace(t2.id)

        assert ws1.root != ws2.root
        assert str(ws1.root).endswith("t1")
        assert str(ws2.root).endswith("t2")
