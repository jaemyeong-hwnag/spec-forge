"""FastAPI 의존성 주입 — 설정 기반으로 어댑터 인스턴스화."""
from functools import lru_cache
from pathlib import Path

from src.adapters.ai.claude_adapter import ClaudeAdapter
from src.adapters.code_scanner.github_scanner import GitHubCodeScanner
from src.adapters.code_scanner.local_scanner import LocalCodeScanner
from src.adapters.notion.notion_adapter import NotionBacklogAdapter
from src.adapters.storage.file_cache import FileRepoCache
from src.adapters.storage.file_storage import FileStorage
from src.core.ports.code_port import RepoRef
from src.core.spec_generator import SpecGenerator
from src.config import get_settings


def get_backlog_adapter():
    s = get_settings()
    provider = s.backlog_provider  # "notion" | 확장 가능
    if provider == "notion":
        return NotionBacklogAdapter(s.notion_token)
    raise ValueError(f"Unknown backlog provider: {provider}")


def get_cache():
    return FileRepoCache(Path(".cache/repos"))


def get_code_adapter(repo_kind: str):
    cache = get_cache()
    if repo_kind == "github":
        return GitHubCodeScanner(Path("repos"), cache)
    return LocalCodeScanner(cache)


def get_ai_adapter():
    s = get_settings()
    if s.ai_provider == "claude-code":
        from src.adapters.ai.claude_code_adapter import ClaudeCodeAdapter
        return ClaudeCodeAdapter(model=s.ai_model)
    return ClaudeAdapter(api_key=s.ai_api_key, model=s.ai_model)


def get_storage():
    s = get_settings()
    return FileStorage(Path(s.output_dir))


def get_spec_generator() -> SpecGenerator:
    s = get_settings()
    cache = get_cache()
    # 복합 코드 스캐너: 레포 종류에 따라 분기
    from src.adapters.code_scanner.composite_scanner import CompositeCodeScanner
    code = CompositeCodeScanner(
        local=LocalCodeScanner(cache),
        github=GitHubCodeScanner(Path("repos"), cache),
    )
    return SpecGenerator(
        backlog=get_backlog_adapter(),
        code=code,
        ai=get_ai_adapter(),
        storage=get_storage(),
    )


def get_repo_refs() -> list[RepoRef]:
    s = get_settings()
    return [RepoRef(name=r["name"], kind=r["type"], source=r["source"]) for r in s.repos]
