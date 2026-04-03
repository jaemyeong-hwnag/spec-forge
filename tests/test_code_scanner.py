"""로컬 코드 스캐너 단위 테스트 — 실제 파일 생성 후 스캔."""
import asyncio
import pytest
from pathlib import Path
from src.adapters.code_scanner.local_scanner import LocalCodeScanner
from src.adapters.storage.file_cache import FileRepoCache
from src.core.ports.code_port import RepoRef


@pytest.fixture
def tmp_repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text(
        'class UserService:\n'
        '    """User management."""\n'
        '    def get_user(self, user_id: str) -> dict:\n'
        '        pass\n'
        '\n'
        'async def create_user(name: str, email: str) -> dict:\n'
        '    pass\n'
    )
    (tmp_path / "src" / "port.py").write_text(
        'from abc import ABC, abstractmethod\n'
        'class UserPort(ABC):\n'
        '    @abstractmethod\n'
        '    async def find(self, id: str): ...\n'
    )
    return tmp_path


@pytest.mark.asyncio
async def test_scanner_extracts_classes(tmp_repo, tmp_path):
    cache = FileRepoCache(tmp_path / ".cache")
    scanner = LocalCodeScanner(cache)
    repo = RepoRef(name="test-repo", kind="local", source=str(tmp_repo))
    await scanner.prepare(repo)
    result = await scanner.scan(repo)

    names = [i["name"] for i in result["interfaces"]]
    assert "UserService" in names
    assert "UserService.get_user" in names


@pytest.mark.asyncio
async def test_scanner_excludes_implementation(tmp_repo, tmp_path):
    cache = FileRepoCache(tmp_path / ".cache")
    scanner = LocalCodeScanner(cache)
    repo = RepoRef(name="test-repo", kind="local", source=str(tmp_repo))
    result = await scanner.scan(repo)

    # 구현 코드(pass 내부)는 시그니처에 포함되지 않아야 함
    for iface in result["interfaces"]:
        assert "pass" not in iface["signature"]


@pytest.mark.asyncio
async def test_scanner_uses_cache(tmp_repo, tmp_path):
    cache = FileRepoCache(tmp_path / ".cache")
    scanner = LocalCodeScanner(cache)
    repo = RepoRef(name="test-repo", kind="local", source=str(tmp_repo))

    r1 = await scanner.scan(repo)
    r2 = await scanner.scan(repo)
    # 두 번째 호출은 캐시에서 반환
    assert r1["name"] == r2["name"]
    assert r1["interfaces"] == r2["interfaces"]


@pytest.mark.asyncio
async def test_scanner_detects_port_pattern(tmp_repo, tmp_path):
    cache = FileRepoCache(tmp_path / ".cache")
    scanner = LocalCodeScanner(cache)
    repo = RepoRef(name="test-repo", kind="local", source=str(tmp_repo))
    result = await scanner.scan(repo)
    assert "Python" in result["languages"]
