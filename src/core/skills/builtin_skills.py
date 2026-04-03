"""
내장 AI 스킬 — 모든 system/user 프롬프트는 ai-token-optimize 원칙 적용:
  placement: role|directives(top) → data(middle) → output_schema(bottom)
  compact k:v: verbose labels → key:value, pipe-separated
  tabular: repeated structures → header once + value rows
  structural tags: lightweight XML, minimal nesting
  constraint_repeat: 핵심 제약을 system 상단 + user 메시지 하단에 반복
    (Liu et al. TACL 2024 "Lost in the Middle" — 중간 컨텍스트 30% 정확도 하락 방지)
  injected_context: 주입 컨텍스트(코드 스캔 등)는 최대 압축
    지시어/스키마 블록은 압축 금지 (LLMLingua-2 원칙)
"""
from .skill_interface import Skill, SkillInput, SkillOutput


def _repeat_constraint(constraint: str, user_xml: str) -> str:
    """핵심 제약을 user 메시지 하단에 반복 — Lost-in-the-Middle 방지."""
    return f"{user_xml}\n<reminder>{constraint}</reminder>"


# ── system prompt 상수 (AI 소비용, 변경 금지) ─────────────────────────────────

_SYS_TEST_PLAN = (
    "role:test-architect\n"
    "task:generate test plan from tech spec\n"
    "rules:cover unit|integration|e2e|edge cases|no implementation code\n"
    "out_schema:\n"
    "<test_plan>\n"
    "  <unit><case desc=\"\" target=\"\"/></unit>\n"
    "  <integration><case desc=\"\" target=\"\"/></integration>\n"
    "  <e2e><case desc=\"\" target=\"\"/></e2e>\n"
    "  <coverage_targets><target file=\"\" min_pct=\"\"/></coverage_targets>\n"
    "</test_plan>"
)

_SYS_TEST_PROMPT_GEN = (
    "role:claude-code-test-prompt-engineer\n"
    "task:generate Claude Code prompt to implement tests from test plan\n"
    "rules:XML optimized|pytest style|mock external deps|no prose\n"
    "out:single <prompt type=\"test\"><![CDATA[...]]></prompt>"
)

_SYS_OPTIMIZE = (
    "role:prompt-token-optimizer\n"
    "rules:strip filler|redundant phrasing|verbose prose\n"
    "preserve:XML tags|technical terms|semantic intent\n"
    "out:optimized prompt only — no commentary no wrapper"
)

_SYS_CODE_PATTERN = (
    "role:code-analyst\n"
    "task:describe EXISTING code structure as-is — do NOT prescribe or recommend new patterns\n"
    "extract:actual_structure|naming_conventions|existing_interfaces\n"
    "rules:"
    "describe only what is present in the scanned code|"
    "if a pattern is partially applied state that|"
    "never suggest migration or refactoring|"
    "identify per-domain patterns (e.g. price domain vs order domain may differ)\n"
    "out_schema:\n"
    "<r>\n"
    "  <patterns><p>name: where_used</p></patterns>\n"
    "  <conventions><c>rule</c></conventions>\n"
    "  <existing_interfaces><i>Name: signature</i></existing_interfaces>\n"
    "</r>"
)

_SYS_SPEC_DRAFT = (
    "role:senior-software-architect\n"
    "task:generate interface-and-requirements spec from backlog item + code context\n"
    "rules:"
    "define WHAT is needed — interfaces, contracts, behaviors — not HOW to implement|"
    "interfaces section: list method signatures, input/output contracts, side effects only|"
    "implementation_plan: feature requirements and acceptance criteria — not coding steps|"
    "follow existing naming conventions from scanned code|"
    "no code snippets|no implementation detail|precise|no filler\n"
    "out_schema:\n"
    "<spec>\n"
    "  <overview/><architecture/><interfaces/>\n"
    "  <implementation_plan><step/></implementation_plan>\n"
    "  <open_questions><q/></open_questions>\n"
    "</spec>"
)

_SYS_SPEC_REFINE = (
    "role:senior-software-architect\n"
    "task:refine spec draft with human decisions\n"
    "rules:human decisions override AI suggestions|no commentary\n"
    "out_schema: same XML structure as draft spec"
)

_SYS_REPO_PROMPT = (
    "role:claude-code-prompt-engineer\n"
    "task:generate ONE prompt for a specific repo that describes WHAT to build — interfaces and feature requirements only\n"
    "rules:"
    "scoped to repo|XML optimized|no prose|"
    "define required interfaces (method names, params, return types, side effects)|"
    "define feature behaviors and acceptance criteria|"
    "do NOT include implementation steps|do NOT specify HOW to code|"
    "let the AI decide implementation from the interface contract\n"
    "documentation_skill:"
    "generated prompt MUST include <documentation> block instructing Claude Code to:"
    "detect language from file extensions|"
    "apply language-native comment format (Java/Kotlin→KDoc/Javadoc, Python→docstring Google-style, JS/TS→JSDoc, Go→doc comment //, Rust→///, Swift→///, C/C++→Doxygen)|"
    "document all public APIs: params, return type, side effects, exceptions|"
    "add OpenAPI 3.x block for any REST endpoint added or modified|"
    "skip private internals and self-evident one-liners\n"
    "out:single <prompt repo=\"{repo_name}\" type=\"implement\"><![CDATA[...]]></prompt>"
)

_SYS_PROMPT_GEN = (
    "role:claude-code-prompt-engineer\n"
    "task:generate 3 Claude Code prompts from finalized spec — each describes WHAT to build, not HOW\n"
    "rules:"
    "XML optimized|minimal tokens|no prose|"
    "implement prompt: required interfaces + feature requirements + acceptance criteria only|"
    "test prompt: what behaviors to verify, not how to write test code|"
    "review prompt: what interface contracts and behaviors to check|"
    "no implementation steps|no code snippets in prompts\n"
    "documentation_skill:"
    "implement prompt MUST include <documentation> block instructing:"
    "detect language from file extensions|"
    "apply language-native comment format (Java/Kotlin→KDoc/Javadoc, Python→docstring Google-style, JS/TS→JSDoc, Go→doc comment //, Rust→///, Swift→///, C/C++→Doxygen)|"
    "document all public APIs: params, return type, side effects, exceptions|"
    "add OpenAPI 3.x block for any REST endpoint added or modified|"
    "skip private internals and self-evident one-liners\n"
    "out_schema:\n"
    "<prompts>\n"
    "  <prompt type=\"implement\"><![CDATA[...]]></prompt>\n"
    "  <prompt type=\"test\"><![CDATA[...]]></prompt>\n"
    "  <prompt type=\"review\"><![CDATA[...]]></prompt>\n"
    "</prompts>"
)


class PromptOptimizerSkill(Skill):
    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "prompt-optimizer"

    @property
    def description(self) -> str:
        return "Strip redundant tokens from AI prompts; preserve XML structure and semantic intent."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        raw = input_.context.get("prompt", "")
        optimized = await self._ai.generate(_SYS_OPTIMIZE, f"<optimize>{raw}</optimize>")
        return SkillOutput(
            skill_name=self.name,
            result={"optimized_prompt": optimized},
            human_summary=f"프롬프트 최적화 완료 ({len(raw)}자 → {len(optimized)}자)",
        )


class CodePatternSkill(Skill):
    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "code-pattern-extract"

    @property
    def description(self) -> str:
        return "Extract arch patterns, conventions, reusable interfaces from code scan."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        code_ctx = input_.context.get("code_context_xml", "")
        result = await self._ai.generate(_SYS_CODE_PATTERN, f"<scan>{code_ctx}</scan>")
        return SkillOutput(
            skill_name=self.name,
            result={"patterns_xml": result},
            human_summary="코드 패턴 추출 완료",
        )


class SpecDraftSkill(Skill):
    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "spec-draft"

    @property
    def description(self) -> str:
        return "Generate initial tech spec from backlog item and code context."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        # 핵심 제약 하단 반복: Lost-in-the-Middle 방지 (Liu et al. TACL 2024)
        user = _repeat_constraint("Reply ONLY with <spec>...</spec> XML. No prose.", input_.to_xml())
        result = await self._ai.generate(_SYS_SPEC_DRAFT, user)
        return SkillOutput(
            skill_name=self.name,
            result={"spec_xml": result},
            human_summary="스펙 초안 생성 완료 — 아키텍처 검토 필요",
        )


class SpecRefineSkill(Skill):
    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "spec-refine"

    @property
    def description(self) -> str:
        return "Refine spec draft with human architecture and interface decisions."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        user = _repeat_constraint("Human decisions are FINAL. Reply ONLY with <spec>...</spec> XML.", input_.to_xml())
        result = await self._ai.generate(_SYS_SPEC_REFINE, user)
        return SkillOutput(
            skill_name=self.name,
            result={"refined_spec_xml": result},
            human_summary="스펙 정제 완료",
        )


class PromptGeneratorSkill(Skill):
    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "prompt-generator"

    @property
    def description(self) -> str:
        return "Generate Claude Code XML prompts (implement/test/review) from finalized spec."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        result = await self._ai.generate(_SYS_PROMPT_GEN, input_.to_xml())
        return SkillOutput(
            skill_name=self.name,
            result={"prompts_xml": result},
            human_summary="AI 실행 프롬프트 3종 생성 (구현/테스트/리뷰)",
        )


class TestPlanSkill(Skill):
    """스펙에서 테스트 계획 생성 — unit/integration/e2e/coverage 목표."""

    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "test-plan"

    @property
    def description(self) -> str:
        return "Generate test plan (unit/integration/e2e/coverage targets) from tech spec."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        result = await self._ai.generate(_SYS_TEST_PLAN, input_.to_xml())
        return SkillOutput(
            skill_name=self.name,
            result={"test_plan_xml": result},
            human_summary="테스트 계획 생성 완료 (unit/integration/e2e)",
        )


class TestPromptSkill(Skill):
    """테스트 계획 → Claude Code 테스트 구현 프롬프트 생성."""

    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "test-prompt-generator"

    @property
    def description(self) -> str:
        return "Generate Claude Code prompt to implement tests from test plan."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        result = await self._ai.generate(_SYS_TEST_PROMPT_GEN, input_.to_xml())
        return SkillOutput(
            skill_name=self.name,
            result={"test_prompt": result},
            human_summary="테스트 구현 프롬프트 생성 완료",
        )


_SYS_PREREQ = (
    "role:prerequisites-analyst\n"
    "task:analyze tech spec and extract ALL pre-conditions needed before AI can successfully implement this\n"
    "categories:\n"
    "  env: environment variables, config values, feature flags\n"
    "  secret: API keys, private keys, tokens, credentials, certificates\n"
    "  service: 3rd-party services that must be accessible (APIs, DBs, external systems)\n"
    "  docs: documentation, API specs, design files that should be provided to AI\n"
    "  dependency: library versions, DB migrations, infrastructure that must exist\n"
    "rules:\n"
    "extract ONLY items that could block or significantly hinder implementation|\n"
    "critical=true if missing would cause implementation to fail|\n"
    "be specific — 'LINE Works RSA private key' not just 'API key'|\n"
    "hint: concrete instruction on how to obtain the item\n"
    "out_schema:\n"
    "<prerequisites>\n"
    "  <item cat=\"\" name=\"\" critical=\"true|false\" hint=\"\"><description/></item>\n"
    "</prerequisites>"
)


class PrerequisiteCheckSkill(Skill):
    """스펙에서 실행 전 필요한 환경/자산/문서 체크리스트 추출."""

    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "prerequisite-check"

    @property
    def description(self) -> str:
        return "Extract pre-conditions (env vars, secrets, docs, services) needed before Claude Code can implement the spec."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        user = _repeat_constraint(
            "Reply ONLY with <prerequisites>...</prerequisites> XML. Be specific and actionable.",
            input_.to_xml(),
        )
        result = await self._ai.generate(_SYS_PREREQ, user)
        return SkillOutput(
            skill_name=self.name,
            result={"prerequisites_xml": result},
            human_summary="실행 전 체크리스트 생성 완료",
        )


class RepoPromptSkill(Skill):
    """레포별 개별 구현 프롬프트 생성 — 병렬 작업 시작에 사용."""

    def __init__(self, ai_port) -> None:
        self._ai = ai_port

    @property
    def name(self) -> str:
        return "repo-prompt-generator"

    @property
    def description(self) -> str:
        return "Generate per-repo scoped implement prompt for parallel Claude Code execution."

    async def execute(self, input_: SkillInput) -> SkillOutput:
        repo_name = input_.context.get("repo_name", "unknown")
        sys = _SYS_REPO_PROMPT.replace("{repo_name}", repo_name)
        user = _repeat_constraint(
            f"Scope ALL changes to repo:{repo_name}. Reply with single <prompt> XML.",
            input_.to_xml(),
        )
        result = await self._ai.generate(sys, user)
        return SkillOutput(
            skill_name=self.name,
            result={"repo_prompt": result, "repo_name": repo_name},
            human_summary=f"{repo_name} 작업 프롬프트 생성 완료",
        )
