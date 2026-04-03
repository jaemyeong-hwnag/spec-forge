"""Notion 백로그 어댑터 — BacklogPort 구현."""
import re

from notion_client import AsyncClient

from src.core.ports.notion_port import BacklogPort


class NotionBacklogAdapter(BacklogPort):
    """notion-client SDK를 사용한 Notion 구현체."""

    def __init__(self, token: str) -> None:
        self._client = AsyncClient(auth=token)

    @property
    def provider_name(self) -> str:
        return "notion"

    async def validate_credentials(self) -> bool:
        try:
            await self._client.users.me()
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_page_id(url: str) -> str:
        """Notion URL에서 페이지 ID 추출.
        지원 형식:
          https://www.notion.so/Title-{id}
          https://www.notion.so/workspace/Title-{id}
          https://notion.so/{id}
        """
        # 마지막 32자리 hex (하이픈 제거 후)
        clean = url.split("?")[0].rstrip("/")
        segment = clean.split("/")[-1]
        # slug-{id} 또는 {id} 형식에서 ID 추출
        hex_id = re.sub(r"[^0-9a-fA-F]", "", segment)[-32:]
        if len(hex_id) != 32:
            raise ValueError(f"URL에서 Notion 페이지 ID를 찾을 수 없습니다: {url}")
        # UUID 형식으로 변환
        return f"{hex_id[:8]}-{hex_id[8:12]}-{hex_id[12:16]}-{hex_id[16:20]}-{hex_id[20:]}"

    async def get_item_by_url(self, url: str) -> dict:
        """URL로 단일 페이지 메타데이터 반환."""
        page_id = self._parse_page_id(url)
        page = await self._client.pages.retrieve(page_id=page_id)
        title = self._extract_title(page)
        return {
            "id": page["id"],
            "title": title,
            "url": page.get("url", url),
            "created_at": page.get("created_time", ""),
        }

    async def get_item_content(self, item_id: str) -> str:
        """페이지의 모든 블록을 재귀적으로 읽어 구조화된 텍스트로 반환."""
        lines: list[str] = []
        await self._collect_blocks(item_id, lines, depth=0)
        raw = "\n".join(lines)
        return self._clean(raw)

    @staticmethod
    def _clean(text: str) -> str:
        """기본 공백/중복 정리만. AI 압축은 SpecGenerator에서 처리."""
        lines = [l.rstrip() for l in text.splitlines()]
        # 연속 빈 줄 → 1개
        out: list[str] = []
        prev_blank = False
        for line in lines:
            blank = not line.strip()
            if blank and prev_blank:
                continue
            out.append(line)
            prev_blank = blank
        # 연속 완전 동일 줄 제거
        deduped: list[str] = []
        for line in out:
            if deduped and line == deduped[-1]:
                continue
            deduped.append(line)
        return "\n".join(deduped)

    def _extract_title(self, page: dict) -> str:
        props = page.get("properties", {})
        for prop in props.values():
            if prop.get("type") == "title":
                parts = prop.get("title", [])
                return "".join(p.get("plain_text", "") for p in parts)
        return "(제목 없음)"

    async def _fetch_blocks(self, block_id: str) -> list[dict]:
        blocks = []
        cursor = None
        while True:
            kwargs: dict = {"block_id": block_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            resp = await self._client.blocks.children.list(**kwargs)
            blocks.extend(resp["results"])
            if not resp.get("has_more"):
                break
            cursor = resp.get("next_cursor")
        return blocks

    async def _collect_blocks(self, block_id: str, lines: list[str], depth: int) -> None:
        """블록을 재귀적으로 수집하여 구조화된 텍스트 생성."""
        if depth > 4:  # 너무 깊은 중첩 방지
            return
        blocks = await self._fetch_blocks(block_id)
        indent = "  " * depth

        for b in blocks:
            btype = b.get("type", "")
            content = b.get(btype, {})
            rich = content.get("rich_text", [])
            text = "".join(r.get("plain_text", "") for r in rich).strip()

            # 블록 타입별 마크다운 형식으로 변환
            if btype == "heading_1":
                lines.append(f"{indent}# {text}")
            elif btype == "heading_2":
                lines.append(f"{indent}## {text}")
            elif btype == "heading_3":
                lines.append(f"{indent}### {text}")
            elif btype == "bulleted_list_item":
                lines.append(f"{indent}- {text}")
            elif btype == "numbered_list_item":
                lines.append(f"{indent}1. {text}")
            elif btype == "to_do":
                checked = content.get("checked", False)
                lines.append(f"{indent}{'[x]' if checked else '[ ]'} {text}")
            elif btype == "callout":
                if text:
                    lines.append(f"{indent}> {text}")
            elif btype == "quote":
                if text:
                    lines.append(f"{indent}> {text}")
            elif btype == "code":
                lang = content.get("language", "")
                if text:
                    lines.append(f"{indent}```{lang}\n{text}\n```")
            elif btype == "divider":
                lines.append(f"{indent}---")
            elif btype in ("paragraph", "toggle"):
                if text:
                    lines.append(f"{indent}{text}")
            elif btype == "table_row":
                cells = content.get("cells", [])
                row_text = " | ".join(
                    "".join(r.get("plain_text", "") for r in cell)
                    for cell in cells
                )
                if row_text:
                    lines.append(f"{indent}{row_text}")
            else:
                if text:
                    lines.append(f"{indent}{text}")

            # 자식 블록 재귀 처리 (토글, 중첩 리스트, 콜아웃 등)
            if b.get("has_children"):
                await self._collect_blocks(b["id"], lines, depth + 1)
