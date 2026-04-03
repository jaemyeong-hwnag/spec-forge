"""Observability 단위 테스트."""
import pytest
from src.core.observability import Tracer


def test_tracer_records_model_call(tmp_path):
    tracer = Tracer(trace_dir=tmp_path)
    tracer.record_model_call(
        name="test.call",
        model="claude-sonnet-4-6",
        input_tokens=100,
        output_tokens=50,
        duration_ms=250.0,
    )
    summary = tracer.get_session_summary()
    assert summary["model_calls"] == 1
    assert summary["total_input_tokens"] == 100
    assert summary["total_output_tokens"] == 50


def test_tracer_flushes_to_file(tmp_path):
    tracer = Tracer(trace_dir=tmp_path)
    cid = tracer.correlation_id
    tracer.record_model_call("t", "m", 10, 5, 100.0)
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    assert cid in files[0].name


def test_tracer_no_pii_in_trace(tmp_path):
    """비밀 정보가 trace에 포함되지 않는지 검증."""
    tracer = Tracer(trace_dir=tmp_path)
    tracer.record_model_call("t", "m", 10, 5, 100.0)
    content = (tmp_path / f"{tracer.correlation_id}.jsonl").read_text()
    # API 키나 토큰이 포함되지 않아야 함
    assert "sk-ant" not in content
    assert "secret_" not in content


def test_correlation_id_propagates():
    tracer = Tracer()
    cid1 = tracer.correlation_id
    tracer.record_model_call("a", "m", 1, 1, 1.0)
    tracer.record_model_call("b", "m", 1, 1, 1.0)
    for entry in tracer._entries:
        assert entry.correlation_id == cid1

    cid2 = tracer.new_correlation_id()
    assert cid2 != cid1
