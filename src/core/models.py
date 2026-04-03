from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class BacklogItem:
    id: str
    title: str
    content: str       # Notion 원문 (사람용 보고에는 가공하여 표시)
    url: str
    created_at: str = ""


@dataclass
class RepoSummary:
    """코드 스캔 결과 — 구조/시그니처만, 구현 코드 없음."""
    name: str
    file_tree: list[str] = field(default_factory=list)
    interfaces: list[dict] = field(default_factory=list)  # {file, name, kind, signature, doc}
    patterns: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)


@dataclass
class HumanDecision:
    architecture_notes: str = ""
    interface_definitions: str = ""
    constraints: str = ""


@dataclass
class AiPrompt:
    """Claude Code에 바로 사용 가능한 AI 최적화 프롬프트."""
    purpose: Literal["implement", "test", "review", "refactor"]
    target_ai: str  # "claude-code" | "claude-api"
    prompt: str     # XML 구조화된 프롬프트 (AI 최적화)
    human_summary: str  # 한 줄 요약 (사람용)


@dataclass
class TechSpec:
    item_id: str
    title: str
    overview: str          # 사람이 읽는 요약
    architecture: str      # 아키텍처 설명
    interfaces: str        # 인터페이스 명세
    implementation_plan: list[str] = field(default_factory=list)
    ai_prompts: list[AiPrompt] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    status: Literal["draft", "human_review", "refined", "complete"] = "draft"
    enabled_skills: list[str] = field(default_factory=lambda: ["test-plan", "test-prompt", "prompt-optimizer"])
    selected_repos: list[str] = field(default_factory=list)  # 스펙 생성 시 선택된 레포 이름 목록
    user_context: str = ""        # 사용자 입력 컨텍스트
    user_constraints: str = ""    # 사용자 입력 제약

    def to_json(self) -> dict:
        return {
            "item_id": self.item_id,
            "title": self.title,
            "overview": self.overview,
            "architecture": self.architecture,
            "interfaces": self.interfaces,
            "implementation_plan": self.implementation_plan,
            "ai_prompts": [
                {
                    "purpose": p.purpose,
                    "target_ai": p.target_ai,
                    "prompt": p.prompt,
                    "human_summary": p.human_summary,
                }
                for p in self.ai_prompts
            ],
            "created_at": self.created_at,
            "status": self.status,
            "selected_repos": self.selected_repos,
            "user_context": self.user_context,
            "user_constraints": self.user_constraints,
        }
