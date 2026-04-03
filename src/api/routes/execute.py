"""병렬 작업 시작 라우트 — 레포별 Claude Code 병렬 실행 + SSE 스트리밍."""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from src.adapters.executor.claude_code_executor import ClaudeCodeExecutor
from src.api.deps import get_repo_refs, get_spec_generator, get_storage
from src.api.templates import templates
from src.config import get_settings
from src.core.observability import get_tracer

router = APIRouter()
_CACHE_DIR = Path(".cache")
_EXEC_DIR = _CACHE_DIR / "exec"


# ── 사용자 선택 가능 스킬 ─────────────────────────────────────────────────────
SELECTABLE_SKILLS: dict[str, dict] = {
    "code-documentation": {
        "label": "주석 / API 스펙",
        "description": "언어별 주석(KDoc·Javadoc·JSDoc·docstring 등) 및 REST OpenAPI 스펙 자동 추가",
        "instruction": (
            "\n<skill name=\"code-documentation\">"
            "detect language from file extensions|"
            "apply native comment format:"
            "Java/Kotlin→KDoc/Javadoc,Python→docstring(Google-style),"
            "JS/TS→JSDoc,Go→doc-comment(//),Rust→///,Swift→///,C/C++→Doxygen|"
            "document all public APIs: params,return type,side effects,exceptions|"
            "add OpenAPI 3.x block adjacent to every REST endpoint added or modified|"
            "skip private internals and self-evident one-liners"
            "</skill>"
        ),
    },
}


# ── repo_prompts 캐시 ─────────────────────────────────────────────────────────

def _save_repo_prompts(item_id: str, prompts: dict[str, str]) -> None:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (_CACHE_DIR / f"repo_prompts_{item_id}.json").write_text(
        json.dumps(prompts, ensure_ascii=False)
    )


def _load_repo_prompts(item_id: str) -> dict[str, str] | None:
    p = _CACHE_DIR / f"repo_prompts_{item_id}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


# ── 실행 로그 / 상태 영속화 ───────────────────────────────────────────────────

def _exec_status_path(item_id: str, repo: str) -> Path:
    return _EXEC_DIR / f"{item_id}_{repo}_status.json"


def _exec_log_path(item_id: str, repo: str) -> Path:
    return _EXEC_DIR / f"{item_id}_{repo}_log.jsonl"


def _save_exec_status(item_id: str, repo: str, status: str, pid: int | None = None) -> None:
    _EXEC_DIR.mkdir(parents=True, exist_ok=True)
    data: dict = {"status": status}
    if pid is not None:
        data["pid"] = pid
    _exec_status_path(item_id, repo).write_text(json.dumps(data))


def _load_exec_status(item_id: str, repo: str) -> str | None:
    p = _exec_status_path(item_id, repo)
    if p.exists():
        try:
            return json.loads(p.read_text()).get("status")
        except Exception:
            pass
    return None


def _load_exec_pid(item_id: str, repo: str) -> int | None:
    p = _exec_status_path(item_id, repo)
    if p.exists():
        try:
            return json.loads(p.read_text()).get("pid")
        except Exception:
            pass
    return None


def _append_exec_log(item_id: str, repo: str, kind: str, val: str) -> None:
    _EXEC_DIR.mkdir(parents=True, exist_ok=True)
    with open(_exec_log_path(item_id, repo), "a", encoding="utf-8") as f:
        f.write(json.dumps({"kind": kind, "val": val}, ensure_ascii=False) + "\n")


def _read_exec_log(item_id: str, repo: str) -> list[dict]:
    p = _exec_log_path(item_id, repo)
    if not p.exists():
        return []
    result = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def _exec_session_path(item_id: str, repo: str) -> Path:
    return _EXEC_DIR / f"{item_id}_{repo}_session.json"


def _save_exec_session(item_id: str, repo: str, session_id: str) -> None:
    _EXEC_DIR.mkdir(parents=True, exist_ok=True)
    _exec_session_path(item_id, repo).write_text(json.dumps({"session_id": session_id}))


def _load_exec_session(item_id: str, repo: str) -> str | None:
    p = _exec_session_path(item_id, repo)
    if p.exists():
        try:
            return json.loads(p.read_text()).get("session_id")
        except Exception:
            pass
    return None


def _clear_exec_state(item_id: str, repo: str) -> None:
    for p in (
        _exec_status_path(item_id, repo),
        _exec_log_path(item_id, repo),
        _exec_session_path(item_id, repo),
    ):
        p.unlink(missing_ok=True)


# ── 실행 이력 (run별 프롬프트 + 로그) ─────────────────────────────────────────

def _exec_runs_meta_path(item_id: str, repo: str) -> Path:
    return _EXEC_DIR / f"{item_id}_{repo}_runs_meta.json"


def _exec_run_log_path(item_id: str, repo: str, run_idx: int) -> Path:
    return _EXEC_DIR / f"{item_id}_{repo}_run_{run_idx}.jsonl"


def _append_run_meta(item_id: str, repo: str, prompt: str) -> int:
    """새 실행 항목 추가. 인덱스 반환."""
    import datetime
    _EXEC_DIR.mkdir(parents=True, exist_ok=True)
    p = _exec_runs_meta_path(item_id, repo)
    runs = []
    if p.exists():
        try:
            runs = json.loads(p.read_text())
        except Exception:
            pass
    runs.append({"ts": datetime.datetime.now().isoformat()[:19], "prompt": prompt, "status": "running"})
    p.write_text(json.dumps(runs, ensure_ascii=False))
    return len(runs) - 1


def _update_run_meta_status(item_id: str, repo: str, run_idx: int, status: str) -> None:
    p = _exec_runs_meta_path(item_id, repo)
    if not p.exists():
        return
    try:
        runs = json.loads(p.read_text())
        if run_idx < len(runs):
            runs[run_idx]["status"] = status
            p.write_text(json.dumps(runs, ensure_ascii=False))
    except Exception:
        pass


def _append_run_log_line(item_id: str, repo: str, run_idx: int, line: str) -> None:
    _EXEC_DIR.mkdir(parents=True, exist_ok=True)
    with open(_exec_run_log_path(item_id, repo, run_idx), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _read_run_history(item_id: str, repo: str) -> list[dict]:
    """전체 실행 이력 (프롬프트 + 로그 + 상태) 반환."""
    p = _exec_runs_meta_path(item_id, repo)
    if not p.exists():
        return []
    try:
        runs = json.loads(p.read_text())
    except Exception:
        return []
    for i, run in enumerate(runs):
        log_p = _exec_run_log_path(item_id, repo, i)
        run["lines"] = log_p.read_text(encoding="utf-8").splitlines() if log_p.exists() else []
    return runs


# ── 기타 헬퍼 ─────────────────────────────────────────────────────────────────

async def _git_auto_commit(repo_path: str, message: str) -> str | None:
    """변경사항이 있으면 자동 커밋 (push 없음). 커밋 해시 반환, 없으면 None."""
    async def _run(cmd: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return proc.returncode, out.decode("utf-8", errors="replace").strip()

    _, status = await _run(["git", "status", "--porcelain"])
    if not status:
        return None

    await _run(["git", "add", "-A"])
    rc, _ = await _run(["git", "commit", "-m", message])
    if rc != 0:
        return None

    _, commit_hash = await _run(["git", "rev-parse", "HEAD"])
    return commit_hash.strip() or None


async def _git_branch_diff(repo_path: str, branch_type: str) -> str:
    """현재 브랜치와 origin base 브랜치 간 diff (로컬 커밋 포함)."""
    async def _run(cmd: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return proc.returncode, out.decode("utf-8", errors="replace")

    if branch_type in ("feature", "bugfix", "release"):
        candidates = ("develop", "development", "main", "master")
    else:
        candidates = ("main", "master", "develop", "development")

    base = candidates[0]
    for candidate in candidates:
        rc, _ = await _run(["git", "rev-parse", "--verify", f"origin/{candidate}"])
        if rc == 0:
            base = candidate
            break

    _, diff = await _run(["git", "diff", f"origin/{base}..HEAD"])
    return diff


def _get_executor() -> ClaudeCodeExecutor:
    s = get_settings()
    return ClaudeCodeExecutor(model=s.ai_model)


async def _git_create_branch(repo_path: str, branch_type: str, branch_name: str) -> list[str]:
    """origin fetch 후 git flow 규칙에 따른 base 브랜치 기준으로 브랜치 생성."""
    full_branch = f"{branch_type}/{branch_name}"
    logs = []

    async def _run(cmd: list[str]) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=repo_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        return proc.returncode, out.decode("utf-8", errors="replace").strip()

    rc, out = await _run(["git", "fetch", "origin"])
    logs.append(f"[git] fetch origin: {'ok' if rc == 0 else out}")

    # git flow: feature/bugfix/release → develop, hotfix → main
    if branch_type in ("feature", "bugfix", "release"):
        candidates = ("develop", "development", "main", "master")
    else:
        candidates = ("main", "master", "develop", "development")

    base = candidates[0]
    for candidate in candidates:
        rc2, _ = await _run(["git", "rev-parse", "--verify", f"origin/{candidate}"])
        if rc2 == 0:
            base = candidate
            break
    logs.append(f"[git] base branch: origin/{base}")

    rc, _ = await _run(["git", "rev-parse", "--verify", full_branch])
    if rc == 0:
        rc2, out2 = await _run(["git", "checkout", full_branch])
        logs.append(f"[git] switched to existing: {full_branch}" if rc2 == 0 else f"[git] checkout error: {out2}")
    else:
        rc, out = await _run(["git", "checkout", "-b", full_branch, f"origin/{base}"])
        logs.append(f"[git] created {full_branch} from origin/{base}" if rc == 0 else f"[git] branch error: {out}")

    return logs


# ── 라우트 ────────────────────────────────────────────────────────────────────

@router.get("/execute/skills")
async def list_selectable_skills():
    return JSONResponse([
        {"id": sid, "label": s["label"], "description": s["description"]}
        for sid, s in SELECTABLE_SKILLS.items()
    ])


@router.post("/execute/{item_id}/prepare", response_class=HTMLResponse)
async def prepare_execution(item_id: str, request: Request):
    storage = get_storage()
    spec_data = await storage.load_spec(item_id)
    if not spec_data:
        return HTMLResponse("<p class='error'>저장된 스펙이 없습니다. 먼저 스펙을 완성하세요.</p>", status_code=404)

    from src.core.models import TechSpec, AiPrompt
    prompts = [AiPrompt(p["purpose"], p["target_ai"], p["prompt"], p["human_summary"]) for p in spec_data.get("ai_prompts", [])]
    spec = TechSpec(
        item_id=spec_data["item_id"], title=spec_data["title"], overview=spec_data["overview"],
        architecture=spec_data["architecture"], interfaces=spec_data["interfaces"],
        implementation_plan=spec_data["implementation_plan"], ai_prompts=prompts, status=spec_data["status"],
        user_context=spec_data.get("user_context", ""),
        user_constraints=spec_data.get("user_constraints", ""),
    )
    all_repos = get_repo_refs()
    if not all_repos:
        return HTMLResponse("<p class='error'>등록된 레포지토리가 없습니다. Settings에서 레포를 추가하세요.</p>")

    selected = spec_data.get("selected_repos", [])
    repos = [r for r in all_repos if r.name in selected] if selected else all_repos

    gen = get_spec_generator()
    repo_prompts = await gen.generate_repo_prompts(spec, repos)
    _save_repo_prompts(item_id, repo_prompts)

    return templates.TemplateResponse(request, "partials/execute_confirm.html", {
        "spec": spec,
        "repo_prompts": repo_prompts,
        "repos": repos,
    })


@router.get("/execute/{item_id}/panel", response_class=HTMLResponse)
async def get_execute_panel(item_id: str, request: Request):
    """캐시된 프롬프트로 실행 패널 반환 — 새로고침 후 복원용 (재생성 없음)."""
    repo_prompts = _load_repo_prompts(item_id)
    if not repo_prompts:
        return HTMLResponse("<p class='error'>프롬프트가 없습니다. 먼저 준비를 실행하세요.</p>")

    storage = get_storage()
    spec_data = await storage.load_spec(item_id)
    if not spec_data:
        return HTMLResponse("<p class='error'>스펙을 찾을 수 없습니다.</p>", status_code=404)

    from src.core.models import TechSpec, AiPrompt
    prompts = [AiPrompt(p["purpose"], p["target_ai"], p["prompt"], p["human_summary"]) for p in spec_data.get("ai_prompts", [])]
    spec = TechSpec(
        item_id=spec_data["item_id"], title=spec_data["title"], overview=spec_data["overview"],
        architecture=spec_data["architecture"], interfaces=spec_data["interfaces"],
        implementation_plan=spec_data["implementation_plan"], ai_prompts=prompts, status=spec_data["status"],
    )
    repos = get_repo_refs()

    return templates.TemplateResponse(request, "partials/execute_confirm.html", {
        "spec": spec,
        "repo_prompts": repo_prompts,
        "repos": repos,
    })


@router.post("/execute/{item_id}/stop/{repo_name}")
async def stop_execution(item_id: str, repo_name: str):
    """실행 중인 Claude Code 프로세스 강제 종료."""
    import signal
    pid = _load_exec_pid(item_id, repo_name)
    if pid:
        try:
            import os
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    _save_exec_status(item_id, repo_name, "stopped")
    msg = f"[중지됨] PID {pid}" if pid else "[중지됨]"
    _append_exec_log(item_id, repo_name, "error", msg)
    # 마지막 run 상태 stopped로 업데이트
    runs = _read_run_history(item_id, repo_name)
    if runs:
        _update_run_meta_status(item_id, repo_name, len(runs) - 1, "stopped")
    return JSONResponse({"status": "stopped", "pid": pid})


@router.get("/execute/{item_id}/history/{repo_name}", response_class=HTMLResponse)
async def get_exec_history(item_id: str, repo_name: str, request: Request):
    runs = _read_run_history(item_id, repo_name)
    return templates.TemplateResponse(request, "partials/exec_history.html", {
        "runs": runs,
        "repo_name": repo_name,
    })


@router.get("/execute/{item_id}/status")
async def get_execution_status(item_id: str):
    """레포별 실행 상태 + 전체 로그 반환 — 페이지 새로고침 후 상태 복원용."""
    repo_prompts = _load_repo_prompts(item_id)
    if not repo_prompts:
        return JSONResponse({})
    result = {}
    for repo in repo_prompts:
        status = _load_exec_status(item_id, repo) or "pending"
        log = _read_exec_log(item_id, repo)
        result[repo] = {
            "status": status,
            "log": log,
        }
    return JSONResponse(result)


@router.get("/execute/{item_id}/stream")
async def stream_execution(
    item_id: str,
    request: Request,
    branch_type: str = "",
    branch_name: str = "",
    only_repo: str = "",
    modification: str = "",  # 수정 요청 텍스트 — 있으면 강제 재실행
    skills: str = "",        # 쉼표 구분 스킬 ID — 프롬프트 뒤에 주입
):
    repo_prompts = _load_repo_prompts(item_id)
    if not repo_prompts:
        async def _err():
            yield "data: {\"error\": \"프롬프트가 없습니다. 먼저 준비를 실행하세요.\"}\n\n"
        return StreamingResponse(_err(), media_type="text/event-stream")

    repos = get_repo_refs()
    repo_map = {r.name: r.source for r in repos}

    storage = get_storage()
    spec_data = await storage.load_spec(item_id)
    spec_title = spec_data.get("title", item_id) if spec_data else item_id

    if only_repo:
        target_prompts = {k: v for k, v in repo_prompts.items() if k == only_repo}
    else:
        target_prompts = repo_prompts

    executor = _get_executor()
    tracer = get_tracer()
    use_branch = bool(branch_type and branch_name)

    # 선택된 스킬 instruction 조합
    skill_instructions = ""
    if skills:
        for sid in skills.split(","):
            sid = sid.strip()
            if sid in SELECTABLE_SKILLS:
                skill_instructions += SELECTABLE_SKILLS[sid]["instruction"]

    # 수정 요청 처리 — resume 또는 git diff 컨텍스트
    resume_session_ids: dict[str, str | None] = {}
    if modification and only_repo and only_repo in target_prompts:
        saved_session = _load_exec_session(item_id, only_repo)
        if saved_session:
            # 이전 세션 이어서 작업
            target_prompts[only_repo] = modification
            resume_session_ids[only_repo] = saved_session
        else:
            # 세션 없음 — 로컬 브랜치 커밋 diff를 컨텍스트로 제공
            repo_path_for_diff = repo_map.get(only_repo, ".")
            diff = await _git_branch_diff(repo_path_for_diff, branch_type)
            context = f"<code_changes>{diff[:4000]}</code_changes>\n\n" if diff.strip() else ""
            target_prompts[only_repo] = f"{context}<modification_request>{modification}</modification_request>"
            resume_session_ids[only_repo] = None

    # 스킬 instruction을 최종 프롬프트 뒤에 주입 (modification 처리 후)
    if skill_instructions:
        target_prompts = {k: v + skill_instructions for k, v in target_prompts.items()}

    async def _sse_generator():
        # 단일 레포 + 기존 실행 상태 확인 (새로고침 복원) — 수정 요청이면 건너뜀
        if only_repo and len(target_prompts) == 1 and not modification:
            existing_status = _load_exec_status(item_id, only_repo)

            if existing_status in ("done", "error"):
                # 완료된 실행: 로그 전체 재생 후 종료
                for entry in _read_exec_log(item_id, only_repo):
                    s = "running" if entry["kind"] == "line" else entry["kind"]
                    yield f"data: {json.dumps({'repo': only_repo, 'status': s, 'line': entry['val']})}\n\n"
                yield f"data: {json.dumps({'status': 'all_done'})}\n\n"
                return

            if existing_status == "running":
                # 진행 중인 실행: 기존 로그 재생 후 tail
                log = _read_exec_log(item_id, only_repo)
                offset = len(log)
                for entry in log:
                    s = "running" if entry["kind"] == "line" else entry["kind"]
                    yield f"data: {json.dumps({'repo': only_repo, 'status': s, 'line': entry['val']})}\n\n"
                # 새 로그 라인 폴링
                keepalive_counter = 0
                while True:
                    if await request.is_disconnected():
                        return
                    current = _read_exec_log(item_id, only_repo)
                    for entry in current[offset:]:
                        s = "running" if entry["kind"] == "line" else entry["kind"]
                        yield f"data: {json.dumps({'repo': only_repo, 'status': s, 'line': entry['val']})}\n\n"
                        if entry["kind"] in ("done", "error"):
                            yield f"data: {json.dumps({'status': 'all_done'})}\n\n"
                            return
                    offset = len(current)
                    keepalive_counter += 1
                    if keepalive_counter % 30 == 0:  # 15초마다 keep-alive
                        yield ": keep-alive\n\n"
                    await asyncio.sleep(0.5)
                return

        # 새 실행 — 기존 상태 초기화 후 시작
        for name in target_prompts:
            _clear_exec_state(item_id, name)
            _save_exec_status(item_id, name, "running")

        if not resume_session_ids:
            resume_session_ids.update({name: None for name in target_prompts})

        queues: dict[str, asyncio.Queue] = {name: asyncio.Queue() for name in target_prompts}

        async def _run_repo(name: str, prompt: str, path: str, resume_sid: str | None, title: str = ""):
            span = tracer.start_span("tool_call", f"execute.{name}", {"repo": name, "executor": executor.executor_name})
            run_idx = _append_run_meta(item_id, name, prompt)
            try:
                if use_branch and not resume_sid:
                    git_logs = await _git_create_branch(path, branch_type, branch_name)
                    for log_line in git_logs:
                        _append_exec_log(item_id, name, "line", log_line)
                        _append_run_log_line(item_id, name, run_idx, log_line)
                        await queues[name].put(("line", log_line))

                async for line in executor.execute(path, prompt, resume_session_id=resume_sid, status_callback=lambda pid: _save_exec_status(item_id, name, "running", pid)):
                    # session_id는 로그에 남기지 않고 저장만
                    if line.startswith("[session_id] "):
                        _save_exec_session(item_id, name, line[13:].strip())
                        continue
                    _append_exec_log(item_id, name, "line", line)
                    _append_run_log_line(item_id, name, run_idx, line)
                    await queues[name].put(("line", line))

                # 완료 후 자동 커밋 (push 없음)
                commit_msg = title if title else name
                commit_hash = await _git_auto_commit(path, commit_msg)
                if commit_hash:
                    msg = f"[git] committed: {commit_hash[:8]}"
                    _append_exec_log(item_id, name, "line", msg)
                    _append_run_log_line(item_id, name, run_idx, msg)
                    await queues[name].put(("line", msg))

                _update_run_meta_status(item_id, name, run_idx, "done")
                _append_exec_log(item_id, name, "done", "")
                _save_exec_status(item_id, name, "done")
                await queues[name].put(("done", None))
            except Exception as e:
                _update_run_meta_status(item_id, name, run_idx, "error")
                _append_exec_log(item_id, name, "error", str(e))
                _save_exec_status(item_id, name, "error")
                await queues[name].put(("error", str(e)))
            finally:
                span.finish()

        tasks = [
            asyncio.create_task(_run_repo(name, prompt, repo_map.get(name, "."), resume_session_ids.get(name), spec_title))
            for name, prompt in target_prompts.items()
        ]

        for name in target_prompts:
            yield f"data: {json.dumps({'repo': name, 'status': 'started', 'line': ''})}\n\n"

        done_set: set[str] = set()
        _ka = 0
        while len(done_set) < len(target_prompts):
            if await request.is_disconnected():
                break
            for name, q in queues.items():
                if name in done_set:
                    continue
                try:
                    kind, val = q.get_nowait()
                    status = "running" if kind == "line" else kind
                    yield f"data: {json.dumps({'repo': name, 'status': status, 'line': val or ''})}\n\n"
                    if kind in ("done", "error"):
                        done_set.add(name)
                except asyncio.QueueEmpty:
                    pass
            await asyncio.sleep(0.05)
            _ka += 1
            if _ka % 300 == 0:  # 15초마다 keep-alive
                yield ": keep-alive\n\n"

        await asyncio.gather(*tasks, return_exceptions=True)
        yield f"data: {json.dumps({'status': 'all_done'})}\n\n"

    return StreamingResponse(
        _sse_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
