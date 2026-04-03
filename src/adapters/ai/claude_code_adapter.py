"""
Claude Code CLI 어댑터 — AiPort 구현.

`claude` CLI 서브프로세스를 통해 현재 실행 중인 Claude Code 세션의 모델을 사용.
API 키 불필요 — Claude Code 로그인 세션 재사용.
"""
import asyncio
import time

from src.core.ports.ai_port import AiPort
from src.core.observability import get_tracer

# full model ID → claude CLI alias
_MODEL_ALIAS = {
    "claude-sonnet-4-6": "sonnet",
    "claude-opus-4-6": "opus",
    "claude-haiku-4-5-20251001": "haiku",
}


class ClaudeCodeAdapter(AiPort):
    """
    claude CLI를 서브프로세스로 호출.
    설정에서 ai.provider = "claude-code" 로 지정 시 사용.
    """

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self._model = _MODEL_ALIAS.get(model, model)

    def provider_name(self) -> str:
        return f"claude-code/{self._model}"

    async def generate(self, system: str, user: str) -> str:
        tracer = get_tracer()
        t0 = time.time()
        error_msg = None

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--print",
                "--system-prompt", system,
                "--model", self._model,
                "--no-session-persistence",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=user.encode()),
                timeout=600,
            )

            if proc.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"claude CLI error (exit {proc.returncode}): {error_msg}")

            return stdout.decode("utf-8", errors="replace").strip()

        except asyncio.TimeoutError:
            error_msg = "timeout 600s"
            raise RuntimeError("claude CLI timeout (600s)")
        except Exception:
            raise
        finally:
            tracer.record_model_call(
                name="claude-code-cli.generate",
                model=self._model,
                input_tokens=0,   # CLI에서 제공 불가
                output_tokens=0,
                duration_ms=round((time.time() - t0) * 1000, 2),
                error=error_msg,
            )
