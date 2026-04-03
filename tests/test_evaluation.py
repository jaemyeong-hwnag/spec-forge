"""Code evaluator 단위 테스트 — 결정론적, 외부 의존성 없음."""
import pytest
from src.core.models import AiPrompt, TechSpec
from src.core.evaluation import (
    eval_structure,
    eval_prompts_format,
    eval_implementation_plan_depth,
    evaluate_spec,
)


def _make_spec(**kwargs) -> TechSpec:
    defaults = dict(
        item_id="test-1",
        title="Test Feature",
        overview="An overview",
        architecture="hexagonal",
        interfaces="Port: foo() -> str",
        implementation_plan=["step 1", "step 2", "step 3"],
        ai_prompts=[
            AiPrompt("implement", "claude-code", "<task>do it</task>", "구현"),
        ],
    )
    defaults.update(kwargs)
    return TechSpec(**defaults)


class TestEvalStructure:
    def test_complete_spec_scores_1(self):
        r = eval_structure(_make_spec())
        assert r.score == 1.0
        assert r.passed is True

    def test_missing_overview_reduces_score(self):
        r = eval_structure(_make_spec(overview=""))
        assert r.score < 1.0
        assert "overview" in r.reason

    def test_missing_all_fields_scores_zero(self):
        r = eval_structure(_make_spec(overview="", architecture="", interfaces="", implementation_plan=[], ai_prompts=[]))
        assert r.score == 0.0
        assert r.passed is False


class TestEvalPromptsFormat:
    def test_xml_prompt_passes(self):
        r = eval_prompts_format(_make_spec())
        assert r.score == 1.0
        assert r.passed is True

    def test_no_prompts_fails(self):
        r = eval_prompts_format(_make_spec(ai_prompts=[]))
        assert r.score == 0.0
        assert r.passed is False

    def test_plain_text_prompt_fails(self):
        plain = AiPrompt("implement", "claude-code", "just do it", "구현")
        r = eval_prompts_format(_make_spec(ai_prompts=[plain]))
        assert r.score == 0.0


class TestEvalPlanDepth:
    def test_three_steps_passes(self):
        r = eval_implementation_plan_depth(_make_spec())
        assert r.passed is True

    def test_one_step_fails(self):
        r = eval_implementation_plan_depth(_make_spec(implementation_plan=["only step"]))
        assert r.passed is False

    def test_five_steps_max_score(self):
        r = eval_implementation_plan_depth(_make_spec(implementation_plan=["s"] * 5))
        assert r.score == 1.0


@pytest.mark.asyncio
async def test_evaluate_spec_no_judge():
    spec = _make_spec()
    report = await evaluate_spec(spec, judge_ai=None)
    assert report.item_id == "test-1"
    assert 0.0 <= report.overall_score <= 1.0
    assert len(report.results) == 3  # code evaluators only
    assert report.to_dict()["item_id"] == "test-1"
