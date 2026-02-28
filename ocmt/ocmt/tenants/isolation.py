"""Filesystem isolation and workspace initialization."""

from __future__ import annotations

from pathlib import Path

from .types import TenantWorkspace


def init_workspace(workspace: TenantWorkspace) -> None:
    """Create the directory tree for a tenant workspace.

    Sets up the same structure OpenClaw uses per-agent
    (workspace/memory/, sessions/) but scoped to a tenant.
    """
    workspace.root.mkdir(parents=True, exist_ok=True)
    workspace.memory_dir.mkdir(parents=True, exist_ok=True)
    workspace.sessions_dir.mkdir(parents=True, exist_ok=True)

    # Seed MEMORY.md if it doesn't exist
    if not workspace.memory_md.exists():
        workspace.memory_md.parent.mkdir(parents=True, exist_ok=True)
        workspace.memory_md.write_text(
            "# Long-term Memory\n\n"
            "Curated knowledge, preferences, and decisions.\n"
        )


def validate_path_within_workspace(workspace: TenantWorkspace, path: str | Path) -> bool:
    """Ensure a path is within the tenant's workspace root.

    Security boundary: prevents path traversal and symlink escape attacks.
    Mirrors OpenClaw's workspace path validation in
    src/memory/manager.ts (readFile method).

    Rejects:
    - Parent-dir traversal (../../etc/passwd)
    - Symlinks that point outside the workspace
    """
    p = Path(path)
    resolved = p.resolve()  # follows symlinks
    workspace_root = workspace.root.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError:
        return False

    # Also reject if any intermediate component is a symlink pointing outside.
    # resolve() already handles the final target, but be explicit.
    if p.is_symlink():
        link_target = p.resolve()
        try:
            link_target.relative_to(workspace_root)
        except ValueError:
            return False

    return True
