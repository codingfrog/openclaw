"""Tests for LLM runner (output parsing, arg building, and compliance)."""

import logging

import pytest

from ocmt.config import CliConfig
from ocmt.llm.runner import _build_args, _parse_output
from ocmt.llm.types import LLMRequest


class TestCliConfigApiKey:
    def test_cli_config_has_api_key_field(self):
        cfg = CliConfig()
        assert cfg.api_key == ""

    def test_cli_config_accepts_api_key(self):
        cfg = CliConfig(api_key="sk-ant-test-key")
        assert cfg.api_key == "sk-ant-test-key"


class TestComplianceWarning:
    @pytest.mark.asyncio
    async def test_warns_when_no_api_key(self, caplog, monkeypatch):
        """Verify compliance warning is logged when no API key is configured."""
        import ocmt.llm.runner as runner_mod

        # Reset the warning flag so it fires again
        runner_mod._compliance_warned = False
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cfg = CliConfig(api_key="")

        with caplog.at_level(logging.WARNING, logger="ocmt.llm.runner"):
            # We can't actually run the CLI (no binary), but the warning
            # fires before subprocess spawn. Catch the subsequent error.
            try:
                await runner_mod.run_cli(
                    request=LLMRequest(prompt="test"),
                    tenant_id="t1",
                    cli_config=cfg,
                    api_key="",
                )
            except (FileNotFoundError, OSError, RuntimeError):
                pass  # CLI binary not available in test env

        assert any("COMPLIANCE WARNING" in r.message for r in caplog.records)
        # Reset for other tests
        runner_mod._compliance_warned = False

    @pytest.mark.asyncio
    async def test_no_warning_when_api_key_set(self, caplog, monkeypatch):
        """No compliance warning when API key is provided."""
        import ocmt.llm.runner as runner_mod

        runner_mod._compliance_warned = False
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        cfg = CliConfig(api_key="sk-ant-test-key-123")

        with caplog.at_level(logging.WARNING, logger="ocmt.llm.runner"):
            try:
                await runner_mod.run_cli(
                    request=LLMRequest(prompt="test"),
                    tenant_id="t2",
                    cli_config=cfg,
                )
            except (FileNotFoundError, OSError, RuntimeError):
                pass

        assert not any("COMPLIANCE WARNING" in r.message for r in caplog.records)
        runner_mod._compliance_warned = False


class TestBuildArgs:
    def test_new_session_args(self):
        req = LLMRequest(
            prompt="Hello",
            model="sonnet",
            system_prompt="Be helpful",
        )
        cfg = CliConfig()
        args = _build_args(req, cfg)

        assert "-p" in args
        assert "--output-format" in args
        assert "json" in args
        assert "--model" in args
        assert "sonnet" in args
        assert "--append-system-prompt" in args
        assert "Be helpful" in args
        assert args[-1] == "Hello"

    def test_resume_args(self):
        req = LLMRequest(
            prompt="Follow up",
            model="sonnet",
            session_id="abc-123",
            resume=True,
        )
        cfg = CliConfig()
        args = _build_args(req, cfg)

        assert "--resume" in args
        assert "abc-123" in args
        assert args[-1] == "Follow up"

    def test_max_turns(self):
        req = LLMRequest(prompt="Test", max_turns=3)
        cfg = CliConfig()
        args = _build_args(req, cfg)
        assert "--max-turns" in args
        assert "3" in args


class TestParseOutput:
    def test_parse_json(self):
        raw = '{"text": "Hello!", "session_id": "sid-1", "usage": {"input_tokens": 10, "output_tokens": 20}}'
        result = _parse_output(raw)
        assert result.text == "Hello!"
        assert result.session_id == "sid-1"
        assert result.usage is not None
        assert result.usage.input_tokens == 10
        assert result.usage.output_tokens == 20

    def test_parse_jsonl(self):
        raw = '{"type": "progress"}\n{"text": "Final answer", "session_id": "s2"}'
        result = _parse_output(raw)
        assert result.text == "Final answer"
        assert result.session_id == "s2"

    def test_parse_plain_text(self):
        raw = "Just a plain text response"
        result = _parse_output(raw)
        assert result.text == "Just a plain text response"

    def test_parse_empty(self):
        result = _parse_output("")
        assert result.text == ""

    def test_parse_nested_content(self):
        raw = '{"content": [{"text": "Part 1"}, {"text": " Part 2"}]}'
        result = _parse_output(raw)
        assert result.text == "Part 1 Part 2"


class TestLLMFallback:
    @pytest.mark.asyncio
    async def test_fallback_on_failure(self):
        from ocmt.llm.fallback import run_with_fallback
        from ocmt.llm.types import LLMResponse

        call_log = []

        async def runner(model: str) -> LLMResponse:
            call_log.append(model)
            if model == "sonnet":
                raise RuntimeError("Model unavailable")
            return LLMResponse(text=f"Response from {model}")

        result = await run_with_fallback(
            models=["sonnet", "haiku"],
            runner=runner,
        )

        assert result.text == "Response from haiku"
        assert "sonnet" in call_log
        assert "haiku" in call_log

    @pytest.mark.asyncio
    async def test_all_models_fail(self):
        from ocmt.llm.fallback import run_with_fallback
        from ocmt.llm.types import LLMResponse

        async def runner(model: str) -> LLMResponse:
            raise RuntimeError(f"{model} failed")

        with pytest.raises(RuntimeError, match="haiku failed"):
            await run_with_fallback(
                models=["sonnet", "haiku"],
                runner=runner,
            )
