"""설정 로더 — config/settings.json 기반, 환경변수 우선."""
import json
import os
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    backlog_provider: str = "notion"
    notion_token: str = ""
    notion_database_id: str = ""
    figma_token: str = ""
    ai_provider: str = "claude"
    ai_api_key: str = ""
    ai_model: str = "claude-sonnet-4-6"
    repos: list[dict] = field(default_factory=list)
    output_dir: str = "./output"
    port: int = 10000
    host: str = "workflow.local"


_SETTINGS_PATH = Path("config/settings.json")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    if not _SETTINGS_PATH.exists():
        return Settings()
    data = json.loads(_SETTINGS_PATH.read_text())
    return Settings(
        backlog_provider=data.get("backlog", {}).get("provider", "notion"),
        notion_token=os.getenv("NOTION_TOKEN") or data.get("backlog", {}).get("notion", {}).get("token", ""),
        notion_database_id=os.getenv("NOTION_DATABASE_ID") or data.get("backlog", {}).get("notion", {}).get("database_id", ""),
        figma_token=os.getenv("FIGMA_TOKEN") or data.get("backlog", {}).get("figma", {}).get("token", ""),
        ai_provider=data.get("ai", {}).get("provider", "claude"),
        ai_api_key=os.getenv("ANTHROPIC_API_KEY") or data.get("ai", {}).get("api_key", ""),
        ai_model=data.get("ai", {}).get("model", "claude-sonnet-4-6"),
        repos=data.get("repos", []),
        output_dir=data.get("output_dir", "./output"),
        port=data.get("port", 10000),
        host=data.get("host", "workflow.local"),
    )


def reload_settings() -> Settings:
    get_settings.cache_clear()
    return get_settings()


def save_settings(data: dict) -> None:
    _SETTINGS_PATH.parent.mkdir(exist_ok=True)
    _SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    reload_settings()
