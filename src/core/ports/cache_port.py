from abc import ABC, abstractmethod
from datetime import datetime


class RepoCachePort(ABC):
    """레포 스캔 결과 캐시 인터페이스. AI 최적화 포맷(XML)으로 저장."""

    @abstractmethod
    async def get(self, repo_key: str) -> dict | None:
        """캐시 히트 시 스캔 결과 반환. 없으면 None."""

    @abstractmethod
    async def set(self, repo_key: str, data: dict, ttl_hours: int = 24) -> None:
        """스캔 결과 저장. TTL 기본 24시간."""

    @abstractmethod
    async def invalidate(self, repo_key: str) -> None:
        """특정 레포 캐시 무효화."""

    @abstractmethod
    async def list_cached(self) -> list[dict]:
        """캐시된 레포 목록 (key, cached_at, expires_at)."""
