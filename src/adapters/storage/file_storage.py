"""파일 기반 스펙 저장소 — StoragePort 구현."""
import json
from datetime import datetime
from pathlib import Path

from src.core.ports.storage_port import StoragePort


class FileStorage(StoragePort):
    def __init__(self, output_dir: Path) -> None:
        self._dir = output_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    async def save_spec(self, item_id: str, spec_md: str, spec_json: dict, prompts_md: str) -> str:
        dest = self._dir / item_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "spec.md").write_text(spec_md, encoding="utf-8")
        (dest / "spec.json").write_text(json.dumps(spec_json, ensure_ascii=False, indent=2))
        (dest / "ai_prompts.md").write_text(prompts_md, encoding="utf-8")
        return str(dest)

    async def load_spec(self, item_id: str) -> dict | None:
        p = self._dir / item_id / "spec.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())

    async def list_specs(self) -> list[dict]:
        result = []
        for d in sorted(self._dir.iterdir()):
            if not d.is_dir():
                continue
            p = d / "spec.json"
            if p.exists():
                data = json.loads(p.read_text())
                result.append({
                    "item_id": data.get("item_id", d.name),
                    "title": data.get("title", ""),
                    "created_at": data.get("created_at", ""),
                    "status": data.get("status", ""),
                })
        return result
