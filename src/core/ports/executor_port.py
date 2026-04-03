from abc import ABC, abstractmethod
from typing import AsyncIterator


class WorkExecutorPort(ABC):
    """
    AI 작업 실행 인터페이스.
    Claude Code, Cursor, Aider 등 어떤 도구든 구현 가능.
    """

    @abstractmethod
    async def execute(self, repo_path: str, prompt: str) -> AsyncIterator[str]:
        """
        레포 디렉토리에서 프롬프트를 실행하고 진행 상황을 스트리밍.
        각 yield: 진행 상황 텍스트 한 줄.
        """

    @property
    @abstractmethod
    def executor_name(self) -> str:
        """e.g. 'claude-code', 'cursor', 'aider'"""

    @property
    @abstractmethod
    def supports_parallel(self) -> bool:
        """병렬 실행 지원 여부."""
