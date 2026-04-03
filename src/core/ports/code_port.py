from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class RepoRef:
    name: str
    kind: str  # "local" | "github"
    source: str  # path or URL


class CodePort(ABC):
    """코드 레포 스캔 인터페이스. 구조/시그니처만 추출, 구현 코드 제외."""

    @abstractmethod
    async def scan(self, repo: RepoRef) -> dict:
        """
        반환 형태:
        {
          "repo_name": str,
          "file_tree": [str],
          "interfaces": [{"file": str, "name": str, "kind": str, "signature": str, "doc": str}],
          "patterns": [str],
          "languages": [str],
        }
        """

    @abstractmethod
    async def prepare(self, repo: RepoRef) -> None:
        """필요 시 clone 등 사전 준비. 이미 준비된 경우 no-op."""
