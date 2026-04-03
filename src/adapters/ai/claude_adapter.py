"""Claude API 어댑터 — AiPort + observability 통합."""
import time
import anthropic

from src.core.ports.ai_port import AiPort
from src.core.observability import get_tracer


class ClaudeAdapter(AiPort):
    def __init__(self, api_key: str, model: str = "claude-sonnet-4-6") -> None:
        # 빈 문자열은 None으로 — Anthropic이 ANTHROPIC_API_KEY 환경변수를 fallback으로 사용
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None)
        self._model = model

    def provider_name(self) -> str:
        return self._model

    async def generate(self, system: str, user: str) -> str:
        tracer = get_tracer()
        t0 = time.time()
        error_msg = None
        try:
            msg = await self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            # 토큰은 API 응답 메타데이터에서 직접 추출 (추정/계산 금지)
            tracer.record_model_call(
                name="claude.generate",
                model=self._model,
                input_tokens=msg.usage.input_tokens,
                output_tokens=msg.usage.output_tokens,
                duration_ms=round((time.time() - t0) * 1000, 2),
            )
            return msg.content[0].text
        except Exception as e:
            error_msg = str(e)
            tracer.record_model_call(
                name="claude.generate",
                model=self._model,
                input_tokens=0,
                output_tokens=0,
                duration_ms=round((time.time() - t0) * 1000, 2),
                error=error_msg,
            )
            raise
