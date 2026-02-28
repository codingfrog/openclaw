"""Tests for LLM runner (output parsing and arg building)."""

import pytest

from ocmt.config import CliConfig
from ocmt.llm.runner import _build_args, _parse_output
from ocmt.llm.types import LLMRequest


class TestCliConfigApiKey:
    def test_cli_config_defaults_to_empty(self):
        cfg = CliConfig()
        assert cfg.api_key == ""

    def test_cli_config_accepts_api_key(self):
        cfg = CliConfig(api_key="sk-ant-test-key")
        assert cfg.api_key == "sk-ant-test-key"


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
