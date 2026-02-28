"""Markdown memory file read/write operations.

Mirrors OpenClaw's pattern of using Markdown files as the canonical
memory store. The database is an index, not the source of truth.

Two memory file types:
- memory/YYYY-MM-DD.md: Daily append-only logs
- MEMORY.md: Curated long-term memory
"""

from __future__ import annotations

import datetime
from pathlib import Path

from ..tenants.types import TenantWorkspace


def daily_log_path(workspace: TenantWorkspace, date: datetime.date | None = None) -> Path:
    """Get the path to a daily memory log file."""
    d = date or datetime.date.today()
    return workspace.memory_dir / f"{d.isoformat()}.md"


def read_daily_log(workspace: TenantWorkspace, date: datetime.date | None = None) -> str | None:
    """Read a daily memory log. Returns None if it doesn't exist."""
    path = daily_log_path(workspace, date)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def append_daily_log(workspace: TenantWorkspace, content: str, date: datetime.date | None = None) -> Path:
    """Append content to today's daily memory log.

    Creates the file if it doesn't exist. Always appends (never overwrites),
    matching OpenClaw's append-only daily log pattern.
    """
    path = daily_log_path(workspace, date)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if not existing.endswith("\n"):
            content = "\n" + content
    else:
        # Create with date header
        d = date or datetime.date.today()
        header = f"# Memory Log — {d.isoformat()}\n\n"
        content = header + content

    with open(path, "a", encoding="utf-8") as f:
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")

    return path


def read_long_term_memory(workspace: TenantWorkspace) -> str | None:
    """Read the long-term MEMORY.md file."""
    if not workspace.memory_md.exists():
        return None
    return workspace.memory_md.read_text(encoding="utf-8")


def write_long_term_memory(workspace: TenantWorkspace, content: str) -> Path:
    """Write to the long-term MEMORY.md file (full overwrite)."""
    workspace.memory_md.parent.mkdir(parents=True, exist_ok=True)
    workspace.memory_md.write_text(content, encoding="utf-8")
    return workspace.memory_md


def load_bootstrap_memory(workspace: TenantWorkspace) -> dict[str, str | None]:
    """Load memory files for session bootstrap.

    Mirrors OpenClaw's bootstrap-files.ts pattern:
    load today + yesterday daily logs + MEMORY.md.
    """
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)

    return {
        "long_term": read_long_term_memory(workspace),
        "daily_today": read_daily_log(workspace, today),
        "daily_yesterday": read_daily_log(workspace, yesterday),
    }


def list_memory_files(workspace: TenantWorkspace) -> list[Path]:
    """List all markdown files in the memory directory + MEMORY.md."""
    files: list[Path] = []

    if workspace.memory_md.exists():
        files.append(workspace.memory_md)

    if workspace.memory_dir.exists():
        files.extend(sorted(workspace.memory_dir.glob("**/*.md")))

    return files
