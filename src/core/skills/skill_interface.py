"""
AI Skill Interface — 스킬 자동 선택 및 실행 레이어.

AI 파이프라인 내부에서 사용. 입출력은 AI 최적화(XML 구조화).
사람에게 노출되는 결과만 마크다운으로 변환.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class SkillInput:
    """AI 최적화 스킬 입력 — XML 태그 기반."""
    skill_name: str
    context: dict[str, Any]   # 구조화된 컨텍스트
    raw_xml: str = ""         # 프리포맷된 XML 프롬프트가 있을 경우

    def to_xml(self) -> str:
        if self.raw_xml:
            return self.raw_xml
        ctx_lines = "\n".join(f"  <{k}>{v}</{k}>" for k, v in self.context.items())
        return f"<skill name=\"{self.skill_name}\">\n{ctx_lines}\n</skill>"


@dataclass
class SkillOutput:
    """AI 최적화 스킬 출력."""
    skill_name: str
    result: dict[str, Any]    # 구조화된 결과 (파이프라인에서 소비)
    human_summary: str        # 사람이 읽는 요약 (UI 표시용)


class Skill(ABC):
    """단일 스킬 인터페이스. 하나의 책임만 가진다."""

    @property
    @abstractmethod
    def name(self) -> str:
        """스킬 식별자."""

    @property
    @abstractmethod
    def description(self) -> str:
        """스킬 설명 (AI 라우터가 선택 판단에 사용)."""

    @abstractmethod
    async def execute(self, input_: SkillInput) -> SkillOutput:
        """스킬 실행."""


class SkillRegistry:
    """스킬 등록 및 조회."""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def list_skills(self) -> list[dict]:
        return [{"name": s.name, "description": s.description} for s in self._skills.values()]

    def available_names(self) -> list[str]:
        return list(self._skills.keys())


class SkillRouter:
    """
    AI가 컨텍스트를 보고 적절한 스킬을 자동 선택.
    내부 통신은 XML, 사람 출력은 마크다운.
    """

    def __init__(self, registry: SkillRegistry, ai_port: Any) -> None:
        self._registry = registry
        self._ai = ai_port

    async def route(self, context: dict[str, Any]) -> list[str]:
        """컨텍스트를 분석해 실행할 스킬 이름 목록 반환 (AI가 판단)."""
        skills_xml = "\n".join(
            f'  <skill name="{s["name"]}">{s["description"]}</skill>'
            for s in self._registry.list_skills()
        )
        context_xml = "\n".join(f"  <{k}>{v}</{k}>" for k, v in context.items())

        system = (
            "You are a skill router. Select which skills to apply based on context. "
            "Reply with ONLY a JSON array of skill names. No explanation."
        )
        user = (
            f"<available_skills>\n{skills_xml}\n</available_skills>\n"
            f"<context>\n{context_xml}\n</context>\n"
            "Which skills should be applied? Reply: [\"skill1\", \"skill2\"]"
        )
        raw = await self._ai.generate(system, user)
        import json, re
        match = re.search(r"\[.*?\]", raw, re.DOTALL)
        if match:
            names = json.loads(match.group())
            return [n for n in names if self._registry.get(n)]
        return []

    async def execute_all(self, skill_names: list[str], input_: SkillInput) -> list[SkillOutput]:
        results = []
        for name in skill_names:
            skill = self._registry.get(name)
            if skill:
                out = await skill.execute(input_)
                results.append(out)
        return results
