"""
스펙 품질 평가 파이프라인 — evaluation 스킬 원칙:
  - code evaluator: 측정 가능한 기준 (구조, 필드 존재, 길이)
  - LLM-as-judge: 의미론적 품질 (완성도, 실행 가능성)
  - 모든 evaluator: score(0.0~1.0) + reason 반환
  - judge 모델 ≠ 생성 모델 (claude-haiku로 평가)
  - 기준선(baseline) 기록
"""
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .models import TechSpec
from .ports.ai_port import AiPort


@dataclass
class EvalResult:
    evaluator: str
    score: float        # 0.0 ~ 1.0
    reason: str
    passed: bool        # score >= threshold


# ── Code Evaluators (deterministic) ──────────────────────────────────────────

def eval_structure(spec: TechSpec) -> EvalResult:
    """필수 필드 존재 여부 검사."""
    missing = []
    if not spec.overview.strip():
        missing.append("overview")
    if not spec.architecture.strip():
        missing.append("architecture")
    if not spec.interfaces.strip():
        missing.append("interfaces")
    if not spec.implementation_plan:
        missing.append("implementation_plan")
    if not spec.ai_prompts:
        missing.append("ai_prompts")

    score = 1.0 - (len(missing) / 5)
    return EvalResult(
        evaluator="structure",
        score=round(score, 2),
        reason=f"Missing: {missing}" if missing else "All required fields present",
        passed=score >= 0.8,
    )


def eval_prompts_format(spec: TechSpec) -> EvalResult:
    """AI 프롬프트 XML 구조 검사."""
    if not spec.ai_prompts:
        return EvalResult("prompts_format", 0.0, "No prompts generated", False)

    valid = sum(1 for p in spec.ai_prompts if "<" in p.prompt and ">" in p.prompt)
    score = valid / len(spec.ai_prompts)
    return EvalResult(
        evaluator="prompts_format",
        score=round(score, 2),
        reason=f"{valid}/{len(spec.ai_prompts)} prompts have XML structure",
        passed=score >= 0.8,
    )


def eval_implementation_plan_depth(spec: TechSpec) -> EvalResult:
    """구현 계획이 충분히 구체적인지 (최소 3단계)."""
    count = len(spec.implementation_plan)
    score = min(count / 5, 1.0)
    return EvalResult(
        evaluator="plan_depth",
        score=round(score, 2),
        reason=f"{count} implementation steps",
        passed=count >= 3,
    )


CODE_EVALUATORS: list[Callable[[TechSpec], EvalResult]] = [
    eval_structure,
    eval_prompts_format,
    eval_implementation_plan_depth,
]

# ── LLM-as-Judge Evaluator ───────────────────────────────────────────────────

_SYS_JUDGE = (
    "role:spec-quality-judge\n"
    "task:evaluate technical spec quality\n"
    "criteria:completeness|actionability|architecture_clarity|prompt_executability\n"
    "rules:score 0.0-1.0|be critical|judge != generator\n"
    "out_schema:<eval><score>0.0-1.0</score><reason>one sentence</reason></eval>"
)


async def eval_llm_quality(spec: TechSpec, judge_ai: AiPort) -> EvalResult:
    """LLM-as-judge: 의미론적 품질 평가. judge 모델 = claude-haiku (생성 모델과 분리)."""
    import re, xml.etree.ElementTree as ET

    user = (
        f"<spec_to_judge>\n"
        f"<title>{spec.title}</title>\n"
        f"<overview>{spec.overview[:500]}</overview>\n"
        f"<architecture>{spec.architecture[:500]}</architecture>\n"
        f"<plan_count>{len(spec.implementation_plan)}</plan_count>\n"
        f"<prompt_count>{len(spec.ai_prompts)}</prompt_count>\n"
        f"</spec_to_judge>"
    )
    raw = await judge_ai.generate(_SYS_JUDGE, user)
    try:
        m = re.search(r"<eval>.*?</eval>", raw, re.DOTALL)
        root = ET.fromstring(m.group() if m else "<eval><score>0.5</score><reason>parse error</reason></eval>")
        score = float(root.findtext("score") or 0.5)
        reason = (root.findtext("reason") or "").strip()
    except Exception:
        score, reason = 0.5, "evaluation parse error"

    return EvalResult(
        evaluator="llm_quality",
        score=round(min(max(score, 0.0), 1.0), 2),
        reason=reason,
        passed=score >= 0.7,
    )


# ── Evaluation Runner ─────────────────────────────────────────────────────────

@dataclass
class EvalReport:
    item_id: str
    results: list[EvalResult]
    overall_score: float
    passed: bool

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "overall_score": self.overall_score,
            "passed": self.passed,
            "results": [
                {"evaluator": r.evaluator, "score": r.score, "reason": r.reason, "passed": r.passed}
                for r in self.results
            ],
        }


async def evaluate_spec(
    spec: TechSpec,
    judge_ai: AiPort | None = None,
    baseline_dir: Path | None = None,
) -> EvalReport:
    results: list[EvalResult] = []

    # 1. Code evaluators (deterministic)
    for fn in CODE_EVALUATORS:
        results.append(fn(spec))

    # 2. LLM-as-judge (선택적 — judge_ai 제공 시)
    if judge_ai:
        results.append(await eval_llm_quality(spec, judge_ai))

    overall = round(sum(r.score for r in results) / len(results), 2)
    report = EvalReport(
        item_id=spec.item_id,
        results=results,
        overall_score=overall,
        passed=all(r.passed for r in results),
    )

    # 3. 기준선 기록 (baseline_dir 제공 시)
    if baseline_dir:
        baseline_dir.mkdir(parents=True, exist_ok=True)
        p = baseline_dir / f"{spec.item_id}_eval.json"
        p.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))

    return report
