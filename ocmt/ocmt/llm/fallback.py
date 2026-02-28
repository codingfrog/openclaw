"""Model fallback chain.

Inspired by OpenClaw's src/agents/model-fallback.ts runWithModelFallback().
When the primary model fails (timeout, error, rate limit), tries fallback models.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

from .types import LLMResponse

logger = logging.getLogger(__name__)


async def run_with_fallback(
    models: list[str],
    runner: Callable[[str], Awaitable[LLMResponse]],
    max_retries: int = 1,
) -> LLMResponse:
    """Try models in order, with retries per model.

    Args:
        models: List of model names to try (e.g. ["sonnet", "haiku"]).
        runner: Async function that takes a model name and returns a response.
        max_retries: Number of retries per model before moving to the next.

    Returns:
        The first successful LLMResponse.

    Raises:
        The last exception if all models fail.
    """
    last_error: Exception | None = None

    for model in models:
        for attempt in range(max_retries + 1):
            try:
                return await runner(model)
            except TimeoutError as exc:
                last_error = exc
                logger.warning(
                    "Model %s attempt %d timed out: %s",
                    model,
                    attempt + 1,
                    exc,
                )
            except RuntimeError as exc:
                last_error = exc
                err_str = str(exc).lower()
                # Don't retry on auth errors — move to next model
                if "unauthorized" in err_str or "forbidden" in err_str:
                    logger.warning(
                        "Model %s auth failure, trying next: %s", model, exc
                    )
                    break
                logger.warning(
                    "Model %s attempt %d failed: %s",
                    model,
                    attempt + 1,
                    exc,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Model %s attempt %d unexpected error: %s",
                    model,
                    attempt + 1,
                    exc,
                )

    raise last_error or RuntimeError("All models failed")
