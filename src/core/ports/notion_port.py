from abc import ABC, abstractmethod


class BacklogPort(ABC):
    """백로그 소스 인터페이스. Notion, Linear, Jira, GitHub Issues 등 어떤 소스든 구현 가능."""

    @abstractmethod
    async def get_item_by_url(self, url: str) -> dict:
        """URL로 단일 아이템 메타데이터 반환.
        반환 형태: {"id": str, "title": str, "url": str}
        """

    @abstractmethod
    async def get_item_content(self, item_id: str) -> str:
        """아이템 전체 텍스트 내용 반환."""

    @abstractmethod
    async def validate_credentials(self) -> bool:
        """자격증명 유효성 검증."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """e.g. 'notion', 'linear', 'jira'"""
