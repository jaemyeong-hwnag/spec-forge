"""
핵심 스펙 생성 파이프라인.

AI 내부 통신: XML (ai-token-optimize 원칙)
  - placement: directives → data → output_schema
  - compact k:v: 속성 기반, pipe 구분
  - tabular: 인터페이스 목록 = header|col1|col2 + 값 행
  - 구현 코드 제외, 시그니처만 포함

출력 결과물: 마크다운 + JSON (사람 최적화)
"""
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .models import AiPrompt, BacklogItem, HumanDecision, RepoSummary, TechSpec
from .ports.ai_port import AiPort
from .ports.code_port import CodePort, RepoRef
from .ports.notion_port import BacklogPort
from .ports.storage_port import StoragePort
from .skills.builtin_skills import (
    CodePatternSkill,
    PrerequisiteCheckSkill,
    PromptGeneratorSkill,
    PromptOptimizerSkill,
    RepoPromptSkill,
    SpecDraftSkill,
    SpecRefineSkill,
    TestPlanSkill,
    TestPromptSkill,
)
from .skills.skill_interface import SkillInput, SkillRegistry

# ── AI 최적화 XML 빌더 ────────────────────────────────────────────────────────

def _ifaces_tabular(interfaces: list[dict]) -> str:
    """
    인터페이스 목록 → tabular 포맷 (ai-token-optimize: tabular 기법)
    header: file|kind|name|sig|doc
    값 행: 탭 구분
    """
    if not interfaces:
        return ""
    rows = ["file\tkind\tname\tsig\tdoc"]
    for i in interfaces:
        doc = (i.get("doc") or "").replace("\n", " ")
        sig = i.get("signature", "")
        rows.append(f"{i['file']}\t{i['kind']}\t{i['name']}\t{sig}\t{doc}")
    return "\n".join(rows)


def _repos_to_xml(repos: list[RepoSummary]) -> str:
    """
    코드 스캔 결과 → AI 최적화 XML
    - 메타데이터는 속성(compact k:v)
    - 인터페이스는 tabular
    - 파일 트리는 pipe 구분
    """
    parts = []
    for r in repos:
        tree_str = "|".join(r.file_tree)
        patterns_str = "|".join(r.patterns)
        langs_str = "|".join(r.languages)
        ifaces = _ifaces_tabular(r.interfaces)
        parts.append(
            f'<repo n="{r.name}" lang="{langs_str}" pat="{patterns_str}">\n'
            f"<tree>{tree_str}</tree>\n"
            f"<ifaces format=\"tsv\">{ifaces}</ifaces>\n"
            f"</repo>"
        )
    return "<repos>" + "".join(parts) + "</repos>"


_CONTENT_COMPRESS_THRESHOLD = 8000  # 이 이상이면 AI 압축 실행

_SYS_CONTENT_COMPRESS = (
    "role:requirements-analyst\n"
    "task:compress backlog content into structured requirements — preserve ALL requirements, policies, acceptance criteria\n"
    "rules:no omission of functional requirements|no omission of policies/rules|remove prose/filler/repeated context|"
    "output must be machine-readable and dense\n"
    "out_schema:\n"
    "<req>\n"
    "  <user_story/>\n"
    "  <requirements><r/></requirements>\n"
    "  <policies><p/></policies>\n"
    "  <acceptance_criteria><ac/></acceptance_criteria>\n"
    "  <constraints/>\n"
    "</req>"
)


def _backlog_to_xml(item: BacklogItem) -> str:
    """백로그 아이템 → AI 최적화 XML (placement: title 상단, content 하단)."""
    return (
        f'<item id="{item.id}">\n'
        f"<title>{item.title}</title>\n"
        f"<content>{item.content}</content>\n"
        f"</item>"
    )


def _human_to_xml(d: HumanDecision) -> str:
    """사람 결정사항 → AI XML (비어있는 필드는 생략)."""
    parts = ["<human>"]
    if d.architecture_notes:
        parts.append(f"<arch>{d.architecture_notes}</arch>")
    if d.interface_definitions:
        parts.append(f"<ifaces>{d.interface_definitions}</ifaces>")
    if d.constraints:
        parts.append(f"<constraints>{d.constraints}</constraints>")
    parts.append("</human>")
    return "\n".join(parts)


# ── XML 파서 ──────────────────────────────────────────────────────────────────

def _strip_fences(text: str) -> str:
    """마크다운 코드 펜스(```xml ... ```) 제거."""
    text = re.sub(r"^```[a-zA-Z]*\n", "", text.strip())
    text = re.sub(r"\n```$", "", text.strip())
    return text.strip()


def _extract_tag(xml_str: str, tag: str) -> str:
    """단일 태그 내용 추출 — ET 실패 시 regex fallback."""
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml_str, re.DOTALL)
    return m.group(1).strip() if m else ""


def _parse_spec_xml(xml_str: str) -> dict:
    xml_str = _strip_fences(xml_str)
    match = re.search(r"<spec>.*?</spec>", xml_str, re.DOTALL)
    if not match:
        return {}
    raw = match.group()

    # ET 시도
    try:
        root = ET.fromstring(raw)
        steps = [e.text or "" for e in root.findall(".//step")]
        return {
            "overview": (root.findtext("overview") or "").strip(),
            "architecture": (root.findtext("architecture") or "").strip(),
            "interfaces": (root.findtext("interfaces") or "").strip(),
            "implementation_plan": [s.strip() for s in steps if s.strip()],
        }
    except ET.ParseError:
        pass

    # regex fallback (특수문자 포함 XML에 대한 방어)
    steps_raw = re.findall(r"<step>(.*?)</step>", raw, re.DOTALL)
    return {
        "overview": _extract_tag(raw, "overview"),
        "architecture": _extract_tag(raw, "architecture"),
        "interfaces": _extract_tag(raw, "interfaces"),
        "implementation_plan": [s.strip() for s in steps_raw if s.strip()],
    }


def _parse_prompts_xml(xml_str: str) -> list[AiPrompt]:
    xml_str = _strip_fences(xml_str)
    prompts: list[AiPrompt] = []
    purpose_label = {
        "implement": "구현 프롬프트 — Claude Code에 직접 붙여넣기",
        "test": "테스트 프롬프트 — 테스트 코드 자동 생성",
        "review": "리뷰 프롬프트 — 코드 리뷰 요청",
    }

    match = re.search(r"<prompts>.*?</prompts>", xml_str, re.DOTALL)
    if not match:
        return prompts
    raw = match.group()

    # ET 시도
    try:
        root = ET.fromstring(raw)
        for elem in root.findall("prompt"):
            ptype = elem.get("type", "implement")
            # CDATA 또는 text
            text = (elem.text or "").strip()
            prompts.append(AiPrompt(
                purpose=ptype,
                target_ai="claude-code",
                prompt=text,
                human_summary=purpose_label.get(ptype, ptype),
            ))
        return prompts
    except ET.ParseError:
        pass

    # regex fallback
    for m in re.finditer(r'<prompt\s+type="([^"]+)"[^>]*>(.*?)</prompt>', raw, re.DOTALL):
        ptype = m.group(1)
        text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(2), flags=re.DOTALL).strip()
        prompts.append(AiPrompt(
            purpose=ptype,
            target_ai="claude-code",
            prompt=text,
            human_summary=purpose_label.get(ptype, ptype),
        ))
    return prompts


# ── 사람용 출력 렌더러 ────────────────────────────────────────────────────────

def _spec_to_markdown(spec: TechSpec) -> str:
    """TechSpec → 사람이 읽는 마크다운. 내부 XML 노출 없음."""
    steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(spec.implementation_plan))
    prompts_md = ""
    for p in spec.ai_prompts:
        prompts_md += f"\n### {p.human_summary}\n\n```xml\n{p.prompt}\n```\n"

    return f"""# {spec.title}

> **Status:** {spec.status} | **Generated:** {spec.created_at[:19]}

## Overview
{spec.overview}

## Architecture
{spec.architecture}

## Interfaces
{spec.interfaces}

## Implementation Plan
{steps}

---

## AI Prompts (Claude Code 실행용)
{prompts_md}
"""


# ── 스킬 레지스트리 초기화 ────────────────────────────────────────────────────

def _build_registry(ai: AiPort) -> SkillRegistry:
    registry = SkillRegistry()
    for skill in [
        CodePatternSkill(ai),
        SpecDraftSkill(ai),
        SpecRefineSkill(ai),
        PromptGeneratorSkill(ai),
        PromptOptimizerSkill(ai),
        RepoPromptSkill(ai),
        TestPlanSkill(ai),
        TestPromptSkill(ai),
        PrerequisiteCheckSkill(ai),
    ]:
        registry.register(skill)
    return registry


# ── 메인 파이프라인 ───────────────────────────────────────────────────────────

class SpecGenerator:
    """
    백로그 아이템 → 기술 스펙 생성 파이프라인.

    Pipeline:
      1. 코드 스캔 (캐시 우선)
      2. code-pattern-extract
      3. spec-draft
      [HUMAN-IN-THE-LOOP]
      4. spec-refine
      5. prompt-generator
      6. prompt-optimizer (각 프롬프트)
      7. 저장 (md + json + ai_prompts.md)
    """

    def __init__(
        self,
        backlog: BacklogPort,
        code: CodePort,
        ai: AiPort,
        storage: StoragePort,
    ) -> None:
        self._backlog = backlog
        self._code = code
        self._ai = ai
        self._storage = storage
        self._registry = _build_registry(ai)

    async def load_backlog(self, source_id: str) -> list[BacklogItem]:
        raw = await self._backlog.list_items(source_id)
        items = []
        for r in raw:
            content = await self._backlog.get_item_content(r["id"])
            items.append(BacklogItem(
                id=r["id"],
                title=r["title"],
                content=content,
                url=r.get("url", ""),
                created_at=r.get("created_at", ""),
            ))
        return items

    async def generate_draft(
        self,
        item: BacklogItem,
        repos: list[RepoRef],
        user_context: str = "",
        user_constraints: str = "",
        figma_context: dict | None = None,
        work_type: str = "backend",
        tech_stack: list[str] | None = None,
        enabled_skills: list[str] | None = None,
        progress=None,  # asyncio.Queue | None
    ) -> TechSpec:
        """스텝 1~3: 코드 스캔 → 패턴 추출 → 초안."""

        async def _emit(step: str, status: str, msg: str = ""):
            if progress is not None:
                await progress.put({"step": step, "status": status, "msg": msg})

        # Step: 레포 코드 스캔
        await _emit("scan", "running", f"{len(repos)}개 레포 스캔 중...")
        repo_summaries: list[RepoSummary] = []
        for repo in repos:
            await _emit("scan", "running", f"{repo.name} 스캔 중...")
            await self._code.prepare(repo)
            raw = await self._code.scan(repo)
            repo_summaries.append(RepoSummary(**raw))
        total_files = sum(len(r.file_tree) for r in repo_summaries)
        await _emit("scan", "done", f"스캔 완료 ({len(repos)}개 레포, {total_files}개 파일)")

        code_xml = _repos_to_xml(repo_summaries)

        # Step: 코드 패턴 추출 (캐시 키 = 레포 이름+파일 수 해시)
        await _emit("pattern", "running", "코드 패턴 분석 중...")
        import hashlib
        pattern_cache_key = f"pattern:{hashlib.md5(code_xml.encode()).hexdigest()[:12]}"
        cached_pattern = await self._code._cache.get(pattern_cache_key) if hasattr(self._code, "_cache") else None
        if cached_pattern:
            pattern_out_result = cached_pattern
            await _emit("pattern", "done", "패턴 분석 완료 (캐시)")
        else:
            pattern_out = await self._registry.get("code-pattern-extract").execute(
                SkillInput("code-pattern-extract", {"code_context_xml": code_xml})
            )
            pattern_out_result = pattern_out.result
            if hasattr(self._code, "_cache"):
                await self._code._cache.set(pattern_cache_key, pattern_out_result, ttl_hours=24)
            await _emit("pattern", "done", "패턴 분석 완료")

        # 사용자 사전 의견 (있을 때만 포함)
        user_xml = ""
        if user_context or user_constraints or tech_stack:
            parts = ["<user_input>"]
            if user_context:
                parts.append(f"<context>{user_context}</context>")
            if user_constraints:
                parts.append(f"<constraints>{user_constraints}</constraints>")
            if tech_stack:
                # 기존 기술 스택 — AI가 이를 깨지 않도록 스펙 생성
                parts.append(f"<existing_stack preserve=\"true\">{' | '.join(tech_stack)}</existing_stack>")
            parts.append("</user_input>")
            user_xml = "\n".join(parts) + "\n"

        # Figma 디자인 컨텍스트 (있을 때만 포함)
        figma_xml = ""
        if figma_context:
            parts = [f'<figma url="{figma_context["url"]}"']
            if figma_context.get("file_name"):
                parts[0] += f' name="{figma_context["file_name"]}"'
            parts[0] += ">"
            for page in figma_context.get("pages", []):
                frames = "|".join(page["frames"])
                parts.append(f'<page name="{page["name"]}">{frames}</page>')
            if figma_context.get("components"):
                parts.append(f'<components>{"|".join(figma_context["components"])}</components>')
            parts.append("</figma>")
            figma_xml = "\n".join(parts) + "\n"

        # 스펙 초안 (AI 최적화 XML: directives(sys) → data(user) → schema(sys 하단))
        draft_xml = (
            f'<task work_type="{work_type}">\n'
            "<directive>Analyze existing_code to understand the current structure. "
            "The spec MUST follow the patterns and conventions already present in existing_code. "
            "Do NOT introduce new architecture patterns not found in existing_code.</directive>\n"
            f"{_backlog_to_xml(item)}\n"
            f"{user_xml}"
            f"{figma_xml}"
            f"<existing_code_analysis>{pattern_out_result.get('patterns_xml', '')}</existing_code_analysis>\n"
            f"<existing_code>{code_xml}</existing_code>\n"
            "</task>"
        )
        # Step: AI 초안 생성
        await _emit("draft", "running", "AI가 스펙 초안 작성 중...")
        draft_out = await self._registry.get("spec-draft").execute(
            SkillInput("spec-draft", {}, raw_xml=draft_xml)
        )
        parsed = _parse_spec_xml(draft_out.result.get("spec_xml", ""))
        await _emit("draft", "done", "초안 생성 완료")

        return TechSpec(
            item_id=item.id,
            title=item.title,
            overview=parsed.get("overview", ""),
            architecture=parsed.get("architecture", ""),
            interfaces=parsed.get("interfaces", ""),
            implementation_plan=parsed.get("implementation_plan", []),
            status="human_review",
            enabled_skills=enabled_skills if enabled_skills is not None else ["test-plan", "test-prompt", "prompt-optimizer"],
            selected_repos=[r.name for r in repos],
            user_context=user_context,
            user_constraints=user_constraints,
        )

    async def refine_with_human(
        self,
        spec: TechSpec,
        decision: HumanDecision,
        repos: list[RepoRef],
    ) -> TechSpec:
        """스텝 4~7: 사람 입력 → 정제 → 프롬프트 생성 → 최적화 → 저장."""

        # 정제 (compact XML: 비어있는 필드 생략)
        refine_xml = (
            "<refine>\n"
            f"<draft><overview>{spec.overview}</overview>"
            f"<arch>{spec.architecture}</arch>"
            f"<ifaces>{spec.interfaces}</ifaces></draft>\n"
            f"{_human_to_xml(decision)}\n"
            "</refine>"
        )
        refine_out = await self._registry.get("spec-refine").execute(
            SkillInput("spec-refine", {}, raw_xml=refine_xml)
        )
        parsed = _parse_spec_xml(refine_out.result.get("refined_spec_xml", ""))
        spec.overview = parsed.get("overview", spec.overview)
        spec.architecture = parsed.get("architecture", spec.architecture)
        spec.interfaces = parsed.get("interfaces", spec.interfaces)
        spec.implementation_plan = parsed.get("implementation_plan", spec.implementation_plan)
        spec.status = "refined"

        # 프롬프트 생성 (compact: steps pipe 구분)
        steps_str = "|".join(spec.implementation_plan[:10])
        repo_names = "|".join(r.name for r in repos)
        prompt_gen_xml = (
            "<spec_for_prompts>\n"
            f'<meta title="{spec.title}" repos="{repo_names}"/>\n'
            f"<arch>{spec.architecture}</arch>\n"
            f"<ifaces>{spec.interfaces}</ifaces>\n"
            f"<steps>{steps_str}</steps>\n"
            "</spec_for_prompts>"
        )
        prompt_out = await self._registry.get("prompt-generator").execute(
            SkillInput("prompt-generator", {}, raw_xml=prompt_gen_xml)
        )
        raw_prompts = _parse_prompts_xml(prompt_out.result.get("prompts_xml", ""))

        # 각 프롬프트 개별 최적화 (스킬 선택에 따라)
        use_optimizer = "prompt-optimizer" in spec.enabled_skills
        optimized: list[AiPrompt] = []
        for p in raw_prompts:
            if use_optimizer:
                opt_out = await self._registry.get("prompt-optimizer").execute(
                    SkillInput("prompt-optimizer", {"prompt": p.prompt})
                )
                p.prompt = opt_out.result.get("optimized_prompt", p.prompt)
            optimized.append(p)

        spec.ai_prompts = optimized
        spec.status = "complete"

        # 저장 — 사람용 마크다운 + 구조화 JSON + AI 프롬프트 모음
        spec_md = _spec_to_markdown(spec)
        prompts_md = "# AI Prompts (Claude Code 실행용)\n\n" + "\n".join(
            f"## {p.human_summary}\n\n```xml\n{p.prompt}\n```\n"
            for p in spec.ai_prompts
        )
        await self._storage.save_spec(spec.item_id, spec_md, spec.to_json(), prompts_md)
        return spec

    async def generate_repo_prompts(
        self,
        spec: TechSpec,
        repos: list[RepoRef],
    ) -> dict[str, str]:
        """
        레포별 개별 구현 프롬프트 생성 (병렬).
        반환: {repo_name: prompt_text}
        병렬 작업 시작 버튼에서 호출.
        """
        import asyncio

        async def _gen_one(repo: RepoRef) -> tuple[str, str]:
            # 레포별 코드 컨텍스트 스캔 (캐시 우선)
            await self._code.prepare(repo)
            raw = await self._code.scan(repo)
            summary = RepoSummary(**raw)
            repo_xml = _repos_to_xml([summary])

            steps_str = "|".join(spec.implementation_plan)  # 전체 구현계획 포함
            user_parts = []
            if spec.user_context:
                user_parts.append(f"<context>{spec.user_context}</context>")
            if spec.user_constraints:
                user_parts.append(f"<constraints>{spec.user_constraints}</constraints>")
            user_xml = f"<user_input>{''.join(user_parts)}</user_input>\n" if user_parts else ""
            inp = SkillInput(
                skill_name="repo-prompt-generator",
                context={"repo_name": repo.name},
                raw_xml=(
                    "<repo_task>\n"
                    f'<meta title="{spec.title}" repo="{repo.name}"/>\n'
                    f"{user_xml}"
                    f"<overview>{spec.overview}</overview>\n"
                    f"<arch>{spec.architecture}</arch>\n"
                    f"<ifaces>{spec.interfaces}</ifaces>\n"
                    f"<steps>{steps_str}</steps>\n"
                    f"{repo_xml}\n"
                    "</repo_task>"
                ),
            )
            out = await self._registry.get("repo-prompt-generator").execute(inp)
            # prompt-optimizer로 토큰 최적화
            opt_out = await self._registry.get("prompt-optimizer").execute(
                SkillInput("prompt-optimizer", {"prompt": out.result.get("repo_prompt", "")})
            )
            prompt = opt_out.result.get("optimized_prompt", out.result.get("repo_prompt", ""))

            # 사용자 입력 원문 명시적 삽입 (AI 해석과 무관하게 항상 포함)
            user_prefix_parts = []
            if spec.user_context:
                user_prefix_parts.append(f"<user_background>{spec.user_context}</user_background>")
            if spec.user_constraints:
                user_prefix_parts.append(f"<user_constraints>{spec.user_constraints}</user_constraints>")
            if user_prefix_parts:
                prompt = "\n".join(user_prefix_parts) + "\n\n" + prompt

            # 스펙 생성 시 선택된 실행 스킬 주입
            from src.api.routes.execute import SELECTABLE_SKILLS
            for sid in (spec.enabled_skills or []):
                if sid in SELECTABLE_SKILLS:
                    prompt += SELECTABLE_SKILLS[sid]["instruction"]

            return repo.name, prompt

        results = await asyncio.gather(*[_gen_one(r) for r in repos])
        return dict(results)
