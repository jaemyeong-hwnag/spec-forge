from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.api.templates import templates
from src.config import get_settings, save_settings, reload_settings

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    s = get_settings()
    return templates.TemplateResponse(request, "settings.html", {"settings": s})


@router.post("/settings", response_class=HTMLResponse)
async def save_settings_route(request: Request):
    form = await request.form()
    data = {
        "backlog": {
            "provider": form.get("backlog_provider", "notion"),
            "notion": {
                "token": form.get("notion_token", ""),
            },
            "figma": {
                "token": form.get("figma_token", ""),
            },
        },
        "ai": {
            "provider": form.get("ai_provider", "claude-code"),
            "api_key": form.get("ai_api_key", ""),
            "model": form.get("ai_model", "claude-sonnet-4-6"),
        },
        "repos": get_settings().repos,
        "output_dir": form.get("output_dir", "./output"),
        "port": int(form.get("port", 10000)),
        "host": form.get("host", "workflow.local"),
    }
    save_settings(data)
    s = reload_settings()
    return templates.TemplateResponse(request, "settings.html", {"settings": s, "saved": True})
