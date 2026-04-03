"""Skill Interface 단위 테스트."""
import pytest
from src.core.skills.skill_interface import Skill, SkillInput, SkillOutput, SkillRegistry


class MockSkill(Skill):
    @property
    def name(self) -> str:
        return "mock-skill"

    @property
    def description(self) -> str:
        return "A mock skill for testing."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        return SkillOutput(
            skill_name=self.name,
            result={"echo": input_.context.get("value", "")},
            human_summary="mock executed",
        )


def test_registry_register_and_get():
    reg = SkillRegistry()
    reg.register(MockSkill())
    assert reg.get("mock-skill") is not None
    assert reg.get("nonexistent") is None


def test_registry_list_skills():
    reg = SkillRegistry()
    reg.register(MockSkill())
    skills = reg.list_skills()
    assert any(s["name"] == "mock-skill" for s in skills)


@pytest.mark.asyncio
async def test_skill_execute_returns_output():
    skill = MockSkill()
    inp = SkillInput("mock-skill", {"value": "hello"})
    out = await skill.execute(inp)
    assert out.result["echo"] == "hello"
    assert out.human_summary == "mock executed"


def test_skill_input_to_xml():
    inp = SkillInput("test", {"key": "val", "count": 3})
    xml = inp.to_xml()
    assert "<skill" in xml
    assert "<key>val</key>" in xml


def test_skill_input_raw_xml_takes_priority():
    inp = SkillInput("test", {}, raw_xml="<custom>data</custom>")
    assert inp.to_xml() == "<custom>data</custom>"
