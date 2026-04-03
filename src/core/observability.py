"""
AI 워크플로우 추적 — observability 스킬 원칙:
  - 모든 AI 모델 호출 → trace entry
  - 토큰 카운트는 API 응답 메타데이터에서 추출 (추정 금지)
  - correlation_id 전파
  - 구조화된 출력만 (자유 형식 문자열 금지)
  - secrets/PII 포함 금지
"""
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class TraceEntry:
    correlation_id: str
    span_type: str          # "model_call" | "tool_call" | "state_transition" | "skill_call"
    name: str
    started_at: float
    ended_at: float = 0.0
    duration_ms: float = 0.0
    metadata: dict = field(default_factory=dict)
    error: str | None = None

    # 모델 호출 전용
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def finish(self, error: str | None = None) -> None:
        self.ended_at = time.time()
        self.duration_ms = round((self.ended_at - self.started_at) * 1000, 2)
        self.error = error

    def to_dict(self) -> dict:
        return asdict(self)


class Tracer:
    """구조화된 AI 워크플로우 추적기."""

    def __init__(self, trace_dir: Path | None = None) -> None:
        self._dir = trace_dir or Path(".traces")
        self._dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[TraceEntry] = []
        self._correlation_id: str = str(uuid.uuid4())

    def new_correlation_id(self) -> str:
        self._correlation_id = str(uuid.uuid4())
        return self._correlation_id

    @property
    def correlation_id(self) -> str:
        return self._correlation_id

    def start_span(self, span_type: str, name: str, metadata: dict | None = None) -> TraceEntry:
        entry = TraceEntry(
            correlation_id=self._correlation_id,
            span_type=span_type,
            name=name,
            started_at=time.time(),
            metadata=metadata or {},
        )
        self._entries.append(entry)
        return entry

    def record_model_call(
        self,
        name: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        duration_ms: float,
        error: str | None = None,
    ) -> TraceEntry:
        """토큰 카운트는 API 응답에서 직접 추출된 값만 허용."""
        entry = TraceEntry(
            correlation_id=self._correlation_id,
            span_type="model_call",
            name=name,
            started_at=time.time() - duration_ms / 1000,
            ended_at=time.time(),
            duration_ms=duration_ms,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            error=error,
        )
        self._entries.append(entry)
        self._flush(entry)
        return entry

    def _flush(self, entry: TraceEntry) -> None:
        log_path = self._dir / f"{self._correlation_id}.jsonl"
        with log_path.open("a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")

    def get_session_summary(self) -> dict:
        model_calls = [e for e in self._entries if e.span_type == "model_call"]
        return {
            "correlation_id": self._correlation_id,
            "total_spans": len(self._entries),
            "model_calls": len(model_calls),
            "total_input_tokens": sum(e.input_tokens for e in model_calls),
            "total_output_tokens": sum(e.output_tokens for e in model_calls),
            "total_duration_ms": sum(e.duration_ms for e in model_calls),
            "errors": [e.name for e in self._entries if e.error],
        }


# 전역 싱글턴 (앱 생명주기와 동일)
_tracer = Tracer()


def get_tracer() -> Tracer:
    return _tracer
