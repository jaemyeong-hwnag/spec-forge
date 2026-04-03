"""Figma API 어댑터 — 디자인 파일 구조 추출."""
import re
import urllib.request
import urllib.error
import json


class FigmaAdapter:
    API_BASE = "https://api.figma.com/v1"

    def __init__(self, token: str) -> None:
        self._token = token

    @staticmethod
    def parse_file_key(url: str) -> str:
        """Figma URL에서 file key 추출.
        지원 형식:
          https://www.figma.com/design/{key}/...
          https://www.figma.com/file/{key}/...
          https://www.figma.com/proto/{key}/...
        """
        m = re.search(r"figma\.com/(?:design|file|proto)/([A-Za-z0-9_-]+)", url)
        if not m:
            raise ValueError(f"Figma URL에서 file key를 찾을 수 없습니다: {url}")
        return m.group(1)

    @staticmethod
    def is_figma_url(url: str) -> bool:
        return "figma.com/" in url

    async def fetch_file_context(self, url: str) -> dict:
        """Figma 파일 구조를 AI 컨텍스트용으로 추출."""
        file_key = self.parse_file_key(url)
        data = await self._get(f"/files/{file_key}?depth=2")
        return self._extract_context(data, url)

    def _extract_context(self, data: dict, url: str) -> dict:
        name = data.get("name", "Untitled")
        doc = data.get("document", {})
        pages = []
        for page in doc.get("children", []):
            frames = [
                c["name"] for c in page.get("children", [])
                if c.get("type") in ("FRAME", "COMPONENT", "COMPONENT_SET")
            ]
            pages.append({"name": page["name"], "frames": frames[:20]})

        components = {
            k: v.get("name", "") for k, v in data.get("components", {}).items()
        }
        component_names = list(components.values())[:30]

        return {
            "file_name": name,
            "url": url,
            "pages": pages,
            "components": component_names,
        }

    async def _get(self, path: str) -> dict:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_sync, path)

    def _get_sync(self, path: str) -> dict:
        req = urllib.request.Request(
            f"{self.API_BASE}{path}",
            headers={"X-Figma-Token": self._token},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise ValueError(f"Figma API 오류 {e.code}: {body[:200]}")
