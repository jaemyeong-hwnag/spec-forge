"""
Claude Code CLI 작업 실행 어댑터 — WorkExecutorPort 구현.
`claude` CLI를 레포 디렉토리에서 서브프로세스로 실행, 출력 스트리밍.
"""
import asyncio
import json
from typing import AsyncIterator

from src.core.ports.executor_port import WorkExecutorPort


class ClaudeCodeExecutor(WorkExecutorPort):
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model = model

    @property
    def executor_name(self) -> str:
        return "claude-code"

    @property
    def supports_parallel(self) -> bool:
        return True

    async def execute(
        self,
        repo_path: str,
        prompt: str,
        resume_session_id: str | None = None,
        status_callback=None,  # callable(pid: int) — 프로세스 시작 직후 호출
    ) -> AsyncIterator[str]:
        cmd = [
            "claude",
            "--print",
            "--model", self._model,
            "--output-format", "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        if resume_session_id:
            cmd += ["--resume", resume_session_id]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=repo_path,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        proc.stdin.write(prompt.encode())
        await proc.stdin.drain()
        proc.stdin.close()

        if status_callback:
            status_callback(proc.pid)

        async for line in proc.stdout:
            raw = line.decode("utf-8", errors="replace").strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
                etype = event.get("type", "")
                if etype == "assistant":
                    for block in event.get("message", {}).get("content", []):
                        if block.get("type") == "text":
                            yield block["text"]
                elif etype == "tool_use":
                    tool = event.get("name", "")
                    inp = event.get("input", {})
                    if tool in ("str_replace_editor", "write_file", "create_file"):
                        yield f"[파일 수정] {inp.get('path', inp.get('file_path', ''))}"
                    elif tool == "bash":
                        yield f"[실행] {str(inp.get('command', ''))[:80]}"
                elif etype == "result":
                    # session_id 추출 — 재개 가능하도록 저장
                    sid = event.get("session_id", "")
                    if sid:
                        yield f"[session_id] {sid}"
                    subtype = event.get("subtype", "")
                    if subtype == "error":
                        yield f"[error] {event.get('error', '')}"
            except (json.JSONDecodeError, KeyError):
                if raw:
                    yield raw

        await proc.wait()
        if proc.returncode != 0:
            stderr_out = await proc.stderr.read()
            err = stderr_out.decode("utf-8", errors="replace").strip()
            if err:
                yield f"[error] {err}"
            yield f"[error] exit code {proc.returncode}"
        else:
            yield "[done]"
