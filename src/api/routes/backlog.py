import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.deps import get_backlog_adapter
from src.api.templates import templates
from src.config import get_settings

router = APIRouter()

_SESSION_FILE = Path(".cache/session.json")


def _load_item() -> dict | None:
    try:
        if _SESSION_FILE.exists():
            return json.loads(_SESSION_FILE.read_text()).get("current_item")
    except Exception:
        pass
    return None


def _save_item(item: dict | None) -> None:
    try:
        _SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SESSION_FILE.write_text(json.dumps({"current_item": item}, ensure_ascii=False))
    except Exception:
        pass


def get_current_item() -> dict | None:
    return _load_item()


@router.get("/backlog", response_class=HTMLResponse)
async def backlog_page(request: Request):
    s = get_settings()
    return templates.TemplateResponse(request, "partials/backlog_list.html", {
        "item": _load_item(),
        "token_set": bool(s.notion_token),
    })


@router.delete("/backlog/current", response_class=HTMLResponse)
async def clear_current(request: Request):
    _save_item(None)
    s = get_settings()
    return templates.TemplateResponse(request, "partials/backlog_list.html", {
        "item": None,
        "token_set": bool(s.notion_token),
    })


@router.get("/api/notion/status", response_class=HTMLResponse)
async def notion_status():
    s = get_settings()
    if not s.notion_token:
        return HTMLResponse(
            '<span class="status-badge status-warn">⚠ Notion 토큰 미설정 — '
            '<a href="/settings">설정하기</a></span>'
        )
    adapter = get_backlog_adapter()
    ok = await adapter.validate_credentials()
    if ok:
        return HTMLResponse('<span class="status-badge status-ok">● Notion 연결됨</span>')
    return HTMLResponse(
        '<span class="status-badge status-err">✕ Notion 연결 실패 — '
        '<a href="/settings">토큰 확인</a></span>'
    )


@router.post("/backlog/fetch", response_class=HTMLResponse)
async def fetch_by_url(request: Request):
    form = await request.form()
    url = form.get("url", "").strip()
    if not url:
        return templates.TemplateResponse(request, "partials/backlog_list.html", {
            "item": None, "error": "URL을 입력하세요.",
        })

    adapter = get_backlog_adapter()

    ok = await adapter.validate_credentials()
    if not ok:
        return templates.TemplateResponse(request, "partials/backlog_list.html", {
            "item": None, "error": "Notion 자격증명이 유효하지 않습니다. Settings에서 토큰을 확인하세요.",
        })

    try:
        item = await adapter.get_item_by_url(url)
    except ValueError as e:
        return templates.TemplateResponse(request, "partials/backlog_list.html", {
            "item": None, "error": str(e),
        })
    except Exception as e:
        return templates.TemplateResponse(request, "partials/backlog_list.html", {
            "item": None, "error": f"Notion API 오류: {e}",
        })

    _save_item(item)
    return templates.TemplateResponse(request, "partials/backlog_list.html", {"item": item})
