"""GitHub URL 코드 스캐너 — clone 후 LocalCodeScanner 위임."""
import shutil
from pathlib import Path

from src.core.ports.cache_port import RepoCachePort
from src.core.ports.code_port import CodePort, RepoRef

from .local_scanner import LocalCodeScanner


class GitHubCodeScanner(CodePort):
    """GitHub URL을 clone한 뒤 LocalCodeScanner로 스캔. 캐시 우선."""

    def __init__(self, repos_dir: Path, cache: RepoCachePort) -> None:
        self._repos_dir = repos_dir
        self._cache = cache
        self._local = LocalCodeScanner(cache)

    async def prepare(self, repo: RepoRef) -> None:
        dest = self._repos_dir / repo.name
        if dest.exists():
            return  # 이미 clone됨
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", repo.source, str(dest),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def scan(self, repo: RepoRef) -> dict:
        cache_key = f"github:{repo.source}"
        cached = await self._cache.get(cache_key)
        if cached:
            return cached

        local_path = self._repos_dir / repo.name
        local_ref = RepoRef(name=repo.name, kind="local", source=str(local_path))
        # 로컬 스캐너가 local: 키로 캐시하므로, github: 키로 별도 저장
        result = await self._local.scan(local_ref)
        result["name"] = repo.name
        await self._cache.set(cache_key, result)
        return result
