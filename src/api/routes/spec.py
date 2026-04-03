import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse

from src.api.deps import get_backlog_adapter, get_repo_refs, get_spec_generator, get_storage
from src.api.routes.backlog import get_current_item
from src.api.templates import templates
from src.config import get_settings
from src.core.evaluation import evaluate_spec
from src.core.models import AiPrompt, BacklogItem, HumanDecision, TechSpec
from src.core.observability import get_tracer

router = APIRouter()
_CACHE_DIR = Path(".cache")


def _pending_path(item_id: str) -> Path:
    return _CACHE_DIR / f"pending_{item_id}.json"


def _draft_path(item_id: str) -> Path:
    return _CACHE_DIR / f"draft_{item_id}.json"


def _save_pending(item_id: str, data: dict) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _pending_path(item_id).write_text(json.dumps(data, ensure_ascii=False))


def _load_pending(item_id: str) -> dict | None:
    p = _pending_path(item_id)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def _delete_pending(item_id: str) -> None:
    _pending_path(item_id).unlink(missing_ok=True)


def _save_draft(item_id: str, draft: TechSpec) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _draft_path(item_id).write_text(json.dumps(draft.to_json(), ensure_ascii=False))


def _load_draft(item_id: str) -> TechSpec | None:
    p = _draft_path(item_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        prompts = [
            AiPrompt(x["purpose"], x["target_ai"], x["prompt"], x["human_summary"])
            for x in data.get("ai_prompts", [])
        ]
        return TechSpec(
            item_id=data["item_id"], title=data["title"], overview=data["overview"],
            architecture=data["architecture"], interfaces=data["interfaces"],
            implementation_plan=data["implementation_plan"], ai_prompts=prompts,
            created_at=data["created_at"], status=data["status"],
            enabled_skills=data.get("enabled_skills", []),
            selected_repos=data.get("selected_repos", []),
            user_context=data.get("user_context", ""),
            user_constraints=data.get("user_constraints", ""),
        )
    except Exception:
        return None


def _delete_draft(item_id: str) -> None:
    _draft_path(item_id).unlink(missing_ok=True)


@router.get("/spec/input/{item_id}", response_class=HTMLResponse)
async def spec_input(item_id: str, request: Request):
    current = get_current_item()
    item_data = current if (current and current["id"] == item_id) else None
    if not item_data:
        return HTMLResponse("<p class='error'>아이템을 찾을 수 없습니다. 먼저 Notion URL을 불러오세요.</p>", status_code=404)
    repos = get_repo_refs()
    s = get_settings()
    return templates.TemplateResponse(request, "partials/pre_generate.html", {
        "item": item_data,
        "repos": repos,
        "figma_token_set": bool(s.figma_token),
    })


@router.post("/spec/generate/{item_id}", response_class=HTMLResponse)
async def generate_spec(item_id: str, request: Request):
    """폼 데이터 저장 후 진행 패널 즉시 반환."""
    current = get_current_item()
    item_data = current if (current and current["id"] == item_id) else None
    if not item_data:
        return HTMLResponse("<p class='error'>아이템을 찾을 수 없습니다.</p>", status_code=404)

    form = await request.form()
    tech_stack = list(form.getlist("tech_stack"))
    custom = form.get("tech_stack_custom", "").strip()
    if custom:
        tech_stack += [t.strip() for t in custom.split(",") if t.strip()]

    _save_pending(item_id, {
        "item_data": item_data,
        "user_context": form.get("user_context", "").strip(),
        "user_constraints": form.get("user_constraints", "").strip(),
        "figma_url": form.get("figma_url", "").strip(),
        "work_type": form.get("work_type", "backend").strip(),
        "selected_repos": list(form.getlist("selected_repos")),
        "tech_stack": tech_stack,
        "skills": list(form.getlist("skills")),
    })
    return templates.TemplateResponse(request, "partials/spec_progress.html", {
        "item_id": item_id,
        "title": item_data["title"],
    })


@router.get("/spec/generate/{item_id}/stream")
async def generate_spec_stream(item_id: str):
    """SSE 스트림으로 스펙 생성 진행 상황 전달."""
    pending = _load_pending(item_id)
    if not pending:
        async def _err():
            yield f'data: {json.dumps({"step": "error", "msg": "폼 데이터가 없습니다. 다시 시도하세요."})}\n\n'
        return StreamingResponse(_err(), media_type="text/event-stream")

    async def _stream():
        queue: asyncio.Queue = asyncio.Queue()

        async def _run():
            try:
                tracer = get_tracer()
                tracer.new_correlation_id()

                item_data = pending["item_data"]
                user_context = pending["user_context"]
                user_constraints = pending["user_constraints"]
                figma_url = pending["figma_url"]
                work_type = pending["work_type"]
                selected_repos = set(pending["selected_repos"])

                # Step 1: Notion 내용 로드 + 필요 시 AI 압축
                await queue.put({"step": "notion", "status": "running", "msg": "Notion 페이지 읽는 중..."})
                adapter = get_backlog_adapter()
                content = await adapter.get_item_content(item_data["id"])
                raw_len = len(content)

                from src.core.spec_generator import _CONTENT_COMPRESS_THRESHOLD, _SYS_CONTENT_COMPRESS
                if raw_len > _CONTENT_COMPRESS_THRESHOLD:
                    import hashlib
                    from src.api.deps import get_ai_adapter
                    from src.core.ports.cache_port import RepoCachePort

                    cache_key = f"content_compress:{hashlib.md5(content.encode()).hexdigest()[:12]}"
                    gen = get_spec_generator()
                    cache: RepoCachePort | None = getattr(getattr(gen, "_code", None), "_cache", None)
                    cached = await cache.get(cache_key) if cache else None

                    if cached:
                        content = cached
                        await queue.put({"step": "notion", "status": "done",
                                         "msg": f"{raw_len}자 → {len(content)}자 (캐시)"})
                    else:
                        await queue.put({"step": "notion", "status": "running",
                                         "msg": f"{raw_len}자 — 요구사항 압축 중..."})
                        from src.api.deps import get_ai_adapter
                        from src.config import get_settings as _gs
                        _s = _gs()
                        # provider에 맞게 haiku 모델로 압축 어댑터 생성
                        if _s.ai_provider == "claude-code":
                            from src.adapters.ai.claude_code_adapter import ClaudeCodeAdapter
                            compressor = ClaudeCodeAdapter(model="claude-haiku-4-5-20251001")
                        else:
                            from src.adapters.ai.claude_adapter import ClaudeAdapter
                            compressor = ClaudeAdapter(api_key=_s.ai_api_key, model="claude-haiku-4-5-20251001")
                        content = await compressor.generate(_SYS_CONTENT_COMPRESS, f"<raw>{content}</raw>")
                        if cache:
                            await cache.set(cache_key, content, ttl_hours=48)
                        await queue.put({"step": "notion", "status": "done",
                                         "msg": f"{raw_len}자 → {len(content)}자 압축 완료"})
                else:
                    await queue.put({"step": "notion", "status": "done", "msg": f"{raw_len}자 로드 완료"})

                item = BacklogItem(
                    id=item_data["id"], title=item_data["title"],
                    content=content, url=item_data.get("url", ""),
                )

                # Figma (있을 때)
                figma_context: dict | None = None
                if figma_url:
                    s = get_settings()
                    from src.adapters.figma.figma_adapter import FigmaAdapter
                    figma = FigmaAdapter(s.figma_token)
                    try:
                        figma_context = await figma.fetch_file_context(figma_url)
                    except Exception:
                        figma_context = {"file_name": "", "url": figma_url, "pages": [], "components": []}

                # Step 2~4: spec_generator에 진행 큐 전달
                gen = get_spec_generator()
                all_repos = get_repo_refs()
                repos = [r for r in all_repos if r.name in selected_repos] if selected_repos else all_repos

                draft = await gen.generate_draft(
                    item, repos,
                    user_context=user_context,
                    user_constraints=user_constraints,
                    figma_context=figma_context,
                    work_type=work_type,
                    tech_stack=pending.get("tech_stack", []),
                    enabled_skills=pending.get("skills", []),
                    progress=queue,
                )
                _save_draft(item_id, draft)
                _delete_pending(item_id)
                await queue.put({"step": "all_done", "item_id": item_id})

            except Exception as e:
                await queue.put({"step": "error", "msg": str(e)})

        task = asyncio.create_task(_run())

        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("step") in ("all_done", "error"):
                break

        await task

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/spec/arch/{item_id}", response_class=HTMLResponse)
async def get_arch_input(item_id: str, request: Request):
    """스트림 완료 후 arch_input 폼 반환."""
    draft = _load_draft(item_id)
    if not draft:
        return HTMLResponse("<p class='error'>초안을 찾을 수 없습니다. 다시 생성하세요.</p>", status_code=404)
    return templates.TemplateResponse(request, "partials/arch_input.html", {"spec": draft})


@router.post("/spec/save/{item_id}", response_class=HTMLResponse)
async def save_spec_draft(item_id: str):
    """AI 정제 없이 현재 초안을 output/에 저장."""
    from src.core.spec_generator import _spec_to_markdown
    draft = _load_draft(item_id)
    if not draft:
        # output/에 저장된 스펙이 있으면 그것도 저장 완료로 처리
        storage = get_storage()
        existing = await storage.load_spec(item_id)
        if existing:
            return HTMLResponse('<span class="badge badge-complete">이미 저장됨</span>')
        return HTMLResponse('<span class="error">저장할 스펙이 없습니다.</span>', status_code=404)

    spec_md = _spec_to_markdown(draft)
    prompts_md = "# AI Prompts\n\n" + "\n".join(
        f"## {p.human_summary}\n\n```xml\n{p.prompt}\n```\n"
        for p in draft.ai_prompts
    )
    storage = get_storage()
    await storage.save_spec(draft.item_id, spec_md, draft.to_json(), prompts_md)
    return HTMLResponse('<span class="badge badge-complete">저장 완료</span>')


@router.post("/spec/refine/{item_id}", response_class=HTMLResponse)
async def refine_spec(item_id: str, request: Request):
    tracer = get_tracer()
    span = tracer.start_span("state_transition", "spec.refine.start", {"item_id": item_id})

    draft = _load_draft(item_id)
    if not draft:
        return HTMLResponse("<p class='error'>초안을 찾을 수 없습니다. 다시 생성하세요.</p>", status_code=404)

    form = await request.form()
    decision = HumanDecision(
        architecture_notes=form.get("architecture_notes", ""),
        interface_definitions=form.get("interface_definitions", ""),
        constraints=form.get("constraints", ""),
    )
    gen = get_spec_generator()
    repos = get_repo_refs()
    spec = await gen.refine_with_human(draft, decision, repos)
    _delete_draft(item_id)

    from src.adapters.ai.claude_adapter import ClaudeAdapter
    s = get_settings()
    judge = ClaudeAdapter(api_key=s.ai_api_key, model="claude-haiku-4-5-20251001")
    report = await evaluate_spec(spec, judge_ai=judge, baseline_dir=Path(".evals"))
    span.finish()

    return templates.TemplateResponse(request, "partials/spec_detail.html", {
        "spec": spec,
        "eval_report": report,
        "trace_summary": tracer.get_session_summary(),
    })


@router.get("/spec/{item_id}", response_class=HTMLResponse)
async def get_spec(item_id: str, request: Request):
    storage = get_storage()
    data = await storage.load_spec(item_id)
    if not data:
        return HTMLResponse("<p class='error'>저장된 스펙이 없습니다.</p>", status_code=404)

    prompts = [
        AiPrompt(p["purpose"], p["target_ai"], p["prompt"], p["human_summary"])
        for p in data.get("ai_prompts", [])
    ]
    spec = TechSpec(
        item_id=data["item_id"], title=data["title"], overview=data["overview"],
        architecture=data["architecture"], interfaces=data["interfaces"],
        implementation_plan=data["implementation_plan"], ai_prompts=prompts,
        created_at=data["created_at"], status=data["status"],
        selected_repos=data.get("selected_repos", []),
        user_context=data.get("user_context", ""),
        user_constraints=data.get("user_constraints", ""),
    )
    return templates.TemplateResponse(request, "partials/spec_detail.html", {"spec": spec})


@router.get("/spec/{item_id}/download/{fmt}")
async def download_spec(item_id: str, fmt: str):
    s = get_settings()
    base = Path(s.output_dir) / item_id
    files = {"md": "spec.md", "json": "spec.json", "prompts": "ai_prompts.md"}
    fname = files.get(fmt)
    if not fname:
        return JSONResponse({"error": "unknown format"}, status_code=400)
    p = base / fname
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, filename=fname)


@router.get("/specs", response_class=HTMLResponse)
async def specs_page(request: Request):
    return templates.TemplateResponse(request, "specs.html", {})


@router.get("/specs/list", response_class=HTMLResponse)
async def list_specs(request: Request):
    storage = get_storage()
    specs = await storage.list_specs()
    return templates.TemplateResponse(request, "partials/specs_list.html", {"specs": specs})


@router.get("/spec/{item_id}/prerequisites", response_class=HTMLResponse)
async def get_prerequisites(item_id: str, request: Request, refresh: bool = False):
    """스펙 기반 실행 전 체크리스트 — 캐시 우선, refresh=true 시 재생성."""
    cache_path = _CACHE_DIR / f"prereq_{item_id}.json"

    if not refresh and cache_path.exists():
        try:
            items = json.loads(cache_path.read_text())
            return templates.TemplateResponse(request, "partials/prerequisites.html", {"items": items, "item_id": item_id})
        except Exception:
            pass

    storage = get_storage()
    spec_data = await storage.load_spec(item_id)
    if not spec_data:
        return HTMLResponse("<p class='muted'>스펙을 찾을 수 없습니다.</p>", status_code=404)

    from src.core.skills.skill_interface import SkillInput
    gen = get_spec_generator()
    skill = gen._registry.get("prerequisite-check")
    if not skill:
        return HTMLResponse("<p class='error'>prerequisite-check 스킬을 찾을 수 없습니다.</p>", status_code=500)

    inp = SkillInput(
        skill_name="prerequisite-check",
        raw_xml=(
            "<spec_analysis>\n"
            f"<title>{spec_data.get('title', '')}</title>\n"
            f"<overview>{spec_data.get('overview', '')}</overview>\n"
            f"<architecture>{spec_data.get('architecture', '')}</architecture>\n"
            f"<interfaces>{spec_data.get('interfaces', '')}</interfaces>\n"
            f"<constraints>{spec_data.get('user_constraints', '')}</constraints>\n"
            f"<plan>{'|'.join(spec_data.get('implementation_plan', []))}</plan>\n"
            "</spec_analysis>"
        ),
        context={},
    )
    out = await skill.execute(inp)
    xml_raw = out.result.get("prerequisites_xml", "")

    # XML 파싱
    import re as _re
    items = []
    for m in _re.finditer(
        r'<item\s+cat="([^"]*?)"\s+name="([^"]*?)"\s+critical="([^"]*?)"\s+hint="([^"]*?)"[^>]*>(.*?)</item>',
        xml_raw, _re.DOTALL
    ):
        items.append({
            "cat": m.group(1),
            "name": m.group(2),
            "critical": m.group(3).lower() == "true",
            "hint": m.group(4),
            "description": m.group(5).strip(),
        })

    cache_path.write_text(json.dumps(items, ensure_ascii=False))
    return templates.TemplateResponse(request, "partials/prerequisites.html", {"items": items, "item_id": item_id})


@router.get("/api/trace/{correlation_id}")
async def get_trace(correlation_id: str):
    p = Path(".traces") / f"{correlation_id}.jsonl"
    if not p.exists():
        return JSONResponse({"error": "trace not found"}, status_code=404)
    entries = [json.loads(line) for line in p.read_text().splitlines() if line]
    return JSONResponse({"correlation_id": correlation_id, "entries": entries})
