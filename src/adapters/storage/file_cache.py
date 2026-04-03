"""파일 기반 레포 스캔 캐시 — RepoCachePort 구현."""
import json
from datetime import datetime, timedelta
from pathlib import Path

from src.core.ports.cache_port import RepoCachePort


class FileRepoCache(RepoCachePort):
    """레포 스캔 결과를 JSON 파일로 캐싱. AI 최적화 포맷 그대로 보존."""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace(":", "_").replace(".", "_")
        return self._dir / f"{safe}.cache.json"

    async def get(self, repo_key: str) -> dict | None:
        p = self._path(repo_key)
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        expires = datetime.fromisoformat(data["expires_at"])
        if datetime.now() > expires:
            p.unlink(missing_ok=True)
            return None
        return data["payload"]

    async def set(self, repo_key: str, data: dict, ttl_hours: int = 24) -> None:
        expires = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
        payload = {
            "key": repo_key,
            "cached_at": datetime.now().isoformat(),
            "expires_at": expires,
            "payload": data,
        }
        self._path(repo_key).write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    async def invalidate(self, repo_key: str) -> None:
        self._path(repo_key).unlink(missing_ok=True)

    async def list_cached(self) -> list[dict]:
        result = []
        for p in self._dir.glob("*.cache.json"):
            try:
                data = json.loads(p.read_text())
                result.append({
                    "key": data["key"],
                    "cached_at": data["cached_at"],
                    "expires_at": data["expires_at"],
                })
            except Exception:
                continue
        return result
