import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.adapters.storage.file_cache import FileRepoCache
from src.api.templates import templates
from src.config import get_settings, save_settings, reload_settings

router = APIRouter()
_cache = FileRepoCache(Path(".cache/repos"))


@router.get("/repos", response_class=HTMLResponse)
async def repos_page(request: Request):
    s = get_settings()
    cached = await _cache.list_cached()
    return templates.TemplateResponse(request, "partials/repos_list.html", {
        "repos": s.repos,
        "cached": {c["key"]: c for c in cached},
    })


@router.post("/repos/add", response_class=HTMLResponse)
async def add_repo(request: Request):
    form = await request.form()
    cfg_path = Path("config/settings.json")
    data = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
    repos = data.get("repos", [])
    repos.append({
        "name": form.get("name", ""),
        "type": form.get("type", "local"),
        "source": form.get("source", ""),
    })
    data["repos"] = repos
    save_settings(data)
    s = reload_settings()
    cached = await _cache.list_cached()
    return templates.TemplateResponse(request, "partials/repos_list.html", {
        "repos": s.repos,
        "cached": {c["key"]: c for c in cached},
    })


@router.delete("/repos/{name}", response_class=HTMLResponse)
async def delete_repo(name: str, request: Request):
    cfg_path = Path("config/settings.json")
    data = json.loads(cfg_path.read_text())
    data["repos"] = [r for r in data.get("repos", []) if r["name"] != name]
    save_settings(data)
    await _cache.invalidate(f"local:{name}")
    await _cache.invalidate(f"github:{name}")
    s = reload_settings()
    cached = await _cache.list_cached()
    return templates.TemplateResponse(request, "partials/repos_list.html", {
        "repos": s.repos,
        "cached": {c["key"]: c for c in cached},
    })


@router.post("/repos/{name}/invalidate", response_class=HTMLResponse)
async def invalidate_cache(name: str, request: Request):
    await _cache.invalidate(f"local:{name}")
    await _cache.invalidate(f"github:{name}")
    s = get_settings()
    cached = await _cache.list_cached()
    return templates.TemplateResponse(request, "partials/repos_list.html", {
        "repos": s.repos,
        "cached": {c["key"]: c for c in cached},
    })
