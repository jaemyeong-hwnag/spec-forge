"""WorkExecutorPort 계약 테스트 — 실제 CLI 없이 mock으로 검증."""
import pytest
from typing import AsyncIterator
from src.core.ports.executor_port import WorkExecutorPort


class MockExecutor(WorkExecutorPort):
    """테스트용 mock executor."""
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    @property
    def executor_name(self) -> str:
        return "mock"

    @property
    def supports_parallel(self) -> bool:
        return True

    async def execute(self, repo_path: str, prompt: str) -> AsyncIterator[str]:
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_executor_yields_lines():
    executor = MockExecutor(["line1", "line2", "[done]"])
    results = []
    async for line in executor.execute("/tmp/repo", "do work"):
        results.append(line)
    assert results == ["line1", "line2", "[done]"]


@pytest.mark.asyncio
async def test_executor_parallel_runs():
    """여러 executor를 asyncio.gather로 동시 실행 검증."""
    import asyncio

    async def collect(executor, path, prompt):
        lines = []
        async for line in executor.execute(path, prompt):
            lines.append(line)
        return lines

    executors = [MockExecutor([f"repo{i}-done"]) for i in range(3)]
    results = await asyncio.gather(*[
        collect(e, f"/tmp/repo{i}", "prompt")
        for i, e in enumerate(executors)
    ])
    assert len(results) == 3
    assert results[0] == ["repo0-done"]
    assert results[1] == ["repo1-done"]
    assert results[2] == ["repo2-done"]


def test_executor_name():
    e = MockExecutor([])
    assert e.executor_name == "mock"


def test_executor_supports_parallel():
    e = MockExecutor([])
    assert e.supports_parallel is True
