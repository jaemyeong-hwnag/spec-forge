from abc import ABC, abstractmethod


class StoragePort(ABC):
    """스펙 저장 인터페이스."""

    @abstractmethod
    async def save_spec(self, item_id: str, spec_md: str, spec_json: dict, prompts_md: str) -> str:
        """저장 후 출력 디렉토리 경로 반환."""

    @abstractmethod
    async def load_spec(self, item_id: str) -> dict | None:
        """저장된 스펙 로드. 없으면 None."""

    @abstractmethod
    async def list_specs(self) -> list[dict]:
        """저장된 스펙 목록 (item_id, title, created_at)."""
