"""ADR 0018 Frontier Hypothesis Generator — `GeneratorPolicy`."""

from __future__ import annotations

import pytest

from app.knowledge_engine.contracts.generator_policy import (
    GeneratorExecutionContext,
    StaticGeneratorPolicy,
)
from app.knowledge_engine.contracts.provenance import GeneratorIdentity

pytestmark = pytest.mark.asyncio


def _context() -> GeneratorExecutionContext:
    return GeneratorExecutionContext(
        repository_id="repo-1",
        commit_sha="abc123",
        generator_identity=GeneratorIdentity(kind="llm", name="test", version="1.0.0"),
    )


async def test_enabled_policy_returns_true() -> None:
    policy = StaticGeneratorPolicy(True)
    assert await policy.should_run(_context()) is True


async def test_disabled_policy_returns_false() -> None:
    policy = StaticGeneratorPolicy(False)
    assert await policy.should_run(_context()) is False
