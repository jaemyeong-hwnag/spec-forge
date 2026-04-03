"""복합 코드 스캐너 — 레포 종류에 따라 local/github 분기."""
from src.core.ports.code_port import CodePort, RepoRef
from .local_scanner import LocalCodeScanner
from .github_scanner import GitHubCodeScanner


class CompositeCodeScanner(CodePort):
    def __init__(self, local: LocalCodeScanner, github: GitHubCodeScanner) -> None:
        self._local = local
        self._github = github
        self._cache = local._cache  # 패턴 분석 캐싱용

    async def prepare(self, repo: RepoRef) -> None:
        await self._adapter(repo).prepare(repo)

    async def scan(self, repo: RepoRef) -> dict:
        return await self._adapter(repo).scan(repo)

    def _adapter(self, repo: RepoRef) -> CodePort:
        return self._github if repo.kind == "github" else self._local
