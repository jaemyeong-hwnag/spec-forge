"""
Spec Forge
  URL: http://workflow.local:10000
  사용 전: echo "127.0.0.1 workflow.local" | sudo tee -a /etc/hosts
"""
import socket
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Request

from src.api.routes import backlog, spec, repos, settings as settings_route, execute
from src.api.templates import templates
from src.config import get_settings


def find_free_port(start: int = 10000) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("localhost", port)) != 0:
                return port
    return start


app = FastAPI(title="Workflow Spec Generator", docs_url=None, redoc_url=None)

app.mount("/static", StaticFiles(directory="web/static"), name="static")

app.include_router(backlog.router)
app.include_router(spec.router)
app.include_router(repos.router)
app.include_router(settings_route.router)
app.include_router(execute.router)

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@app.get("/specs")
async def specs_page(request: Request):
    from fastapi.responses import HTMLResponse
    from src.api.deps import get_storage
    storage = get_storage()
    specs_list = await storage.list_specs()
    return templates.TemplateResponse(request, "partials/specs_list.html", {"specs": specs_list})


@app.get("/health")
async def health():
    from src.core.observability import get_tracer
    return {"status": "ok", "trace_session": get_tracer().correlation_id}


if __name__ == "__main__":
    s = get_settings()
    port = find_free_port(s.port)
    print(f"\n  Workflow Spec Generator")
    print(f"  http://{s.host}:{port}")
    print(f"  (로컬: http://localhost:{port})\n")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
