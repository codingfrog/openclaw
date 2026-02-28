"""Configuration loading and Pydantic models."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class CliConfig(BaseModel):
    """Claude Code CLI backend configuration.

    Ported from OpenClaw's CliBackendConfig (src/config/types.agent-defaults.ts).
    """

    command: str = "claude"
    args: list[str] = Field(
        default_factory=lambda: ["-p", "--output-format", "json", "--no-permissions"]
    )
    resume_args: list[str] = Field(
        default_factory=lambda: [
            "-p",
            "--output-format",
            "json",
            "--no-permissions",
            "--resume",
            "{session_id}",
        ]
    )
    model_arg: str = "--model"
    session_arg: str = "--session-id"
    system_prompt_arg: str = "--append-system-prompt"
    timeout_ms: int = 120_000
    no_output_timeout_ms: int = 60_000
    serialize: bool = True
    # Optional Anthropic API key. When set, passed to CLI subprocess.
    # If empty, the CLI uses its own auth (OAuth session or env key).
    api_key: str = ""


class AgentConfig(BaseModel):
    """Agent defaults."""

    default_model: str = "sonnet"
    fallback_models: list[str] = Field(default_factory=lambda: ["haiku"])
    cli: CliConfig = Field(default_factory=CliConfig)


class MemoryConfig(BaseModel):
    """Memory search settings.

    Defaults ported from OpenClaw's ResolvedMemorySearchConfig.
    """

    chunk_tokens: int = 400
    chunk_overlap: int = 80
    max_results: int = 6
    min_score: float = 0.35
    vector_weight: float = 0.7
    text_weight: float = 0.3
    temporal_decay_enabled: bool = False
    temporal_decay_half_life_days: int = 30


class DatabaseConfig(BaseModel):
    """Global database settings."""

    path: str = "ocmt.sqlite"


class ApiConfig(BaseModel):
    """API server settings."""

    host: str = "0.0.0.0"
    port: int = 8000


class AppConfig(BaseModel):
    """Top-level application configuration."""

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    tenant_workspace_root: str = "data/tenants"
    agents: AgentConfig = Field(default_factory=AgentConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)


def load_config(config_path: str | Path = "config.yaml") -> AppConfig:
    """Load configuration from a YAML file, falling back to defaults."""
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        return AppConfig.model_validate(raw)
    return AppConfig()
