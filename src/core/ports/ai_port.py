from abc import ABC, abstractmethod


class AiPort(ABC):
    """AI 제공자 인터페이스. 텍스트 생성만 담당."""

    @abstractmethod
    async def generate(self, system: str, user: str) -> str:
        """system + user 프롬프트로 텍스트 생성."""

    @abstractmethod
    def provider_name(self) -> str:
        """e.g. 'claude-sonnet-4-6'"""

    def preferred_format(self) -> str:
        """
        AI 최적화 포맷 힌트 (arXiv:2411.10541):
          'xml'  — Claude 계열 (훈련 데이터 기반, XML 최적)
          'yaml' — GPT/Gemini 등 비-Claude (YAML이 17.7pt 우위)
        파이프라인이 이 값을 보고 컨텍스트 직렬화 포맷을 전환한다.
        """
        return "xml"
