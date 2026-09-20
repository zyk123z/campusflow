from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from app.core.enums import IntentType, RiskLevel
from app.models.entities import CampusEventReport, UserAccount


class SkillLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkillValidationIssue:
    level: str
    message: str


@dataclass(frozen=True)
class CampusFlowSkill:
    name: str
    description: str
    body: str
    path: Path
    metadata: dict[str, str] = field(default_factory=dict)

    def prompt_context(self) -> str:
        return f"应用 skill: {self.name}\n{self.body.strip()}"

    def validation_issues(self) -> list[SkillValidationIssue]:
        issues: list[SkillValidationIssue] = []
        if self.path.parent.name != self.name:
            issues.append(SkillValidationIssue("WARN", f"目录名 {self.path.parent.name} 与 skill name {self.name} 不一致"))
        if "## Workflow" not in self.body:
            issues.append(SkillValidationIssue("WARN", "建议包含 ## Workflow 小节，便于人工审阅和模型稳定加载"))
        if len(self.description) < 20:
            issues.append(SkillValidationIssue("WARN", "description 太短，可能无法准确表达触发场景"))
        if self.name == "staff_handoff_summary" and "```text" not in self.body:
            issues.append(SkillValidationIssue("ERROR", "staff_handoff_summary 必须包含 text 模板"))
        return issues


class CampusFlowSkillRegistry:
    def __init__(self, root: Path | None = None):
        self.root = root or Path(__file__).resolve().parents[2] / "skills"

    def list_skills(self) -> list[CampusFlowSkill]:
        if not self.root.exists():
            return []
        skills = []
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            skills.append(self._load_skill_file(skill_file))
        return skills

    def status_items(self) -> list[dict]:
        if not self.root.exists():
            return []
        items = []
        for skill_file in sorted(self.root.glob("*/SKILL.md")):
            try:
                skill = self._load_skill_file(skill_file)
                issues = skill.validation_issues()
            except SkillLoadError as exc:
                items.append(
                    {
                        "name": skill_file.parent.name,
                        "status": "FAILED",
                        "description": str(exc),
                        "path": str(skill_file.relative_to(self.root.parent)),
                        "issues": [{"level": "ERROR", "message": str(exc)}],
                    }
                )
                continue
            has_error = any(issue.level == "ERROR" for issue in issues)
            items.append(
                {
                    "name": skill.name,
                    "status": "FAILED" if has_error else "READY" if not issues else "WARN",
                    "description": skill.description,
                    "path": str(skill.path.relative_to(self.root.parent)),
                    "issues": [{"level": issue.level, "message": issue.message} for issue in issues],
                    "metadata": skill.metadata,
                }
            )
        return items

    def get_required(self, name: str) -> CampusFlowSkill:
        for skill in self.list_skills():
            if skill.name == name:
                return skill
        raise SkillLoadError(f"required standard skill not found: {name}")

    def template_for(self, name: str) -> str:
        skill = self.get_required(name)
        match = re.search(r"```text\s*\n(?P<template>.*?)\n```", skill.body, re.DOTALL)
        if match is None:
            raise SkillLoadError(f"standard skill {name} does not define a text template")
        return match.group("template").strip()

    def _load_skill_file(self, path: Path) -> CampusFlowSkill:
        text = path.read_text(encoding="utf-8")
        metadata, body = _split_frontmatter(text, path)
        name = metadata.get("name") or path.parent.name
        description = metadata.get("description", "")
        if not name.strip():
            raise SkillLoadError(f"{path} is missing frontmatter name")
        if not description.strip():
            raise SkillLoadError(f"{path} is missing frontmatter description")
        if not body.strip():
            raise SkillLoadError(f"{path} is missing skill body")
        return CampusFlowSkill(name=name.strip(), description=description.strip(), body=body.strip(), path=path, metadata=metadata)


class CampusFlowSkillLibrary:
    @staticmethod
    def registry() -> CampusFlowSkillRegistry:
        return CampusFlowSkillRegistry()

    @staticmethod
    def list_skills() -> list[CampusFlowSkill]:
        return CampusFlowSkillLibrary.registry().list_skills()

    @staticmethod
    def status_items() -> list[dict]:
        return CampusFlowSkillLibrary.registry().status_items()

    @staticmethod
    def response_skill_context(intent: IntentType, risk: RiskLevel, text: str) -> str:
        names = CampusFlowSkillLibrary.response_skill_names(intent, risk, text)
        registry = CampusFlowSkillLibrary.registry()
        return "\n\n".join(registry.get_required(name).prompt_context() for name in names)

    @staticmethod
    def response_skill_names(intent: IntentType, risk: RiskLevel, text: str) -> list[str]:
        if intent == IntentType.CHAT:
            return []

        if risk == RiskLevel.HIGH:
            return ["policy_answer_baseline", "campus_emergency_response"]

        lowered = text.lower()
        names = ["policy_answer_baseline"]
        if intent == IntentType.COMPLAINT or _contains_any(lowered, ["投诉", "举报", "申诉", "推诿", "一直没人处理"]):
            names.append("complaint_handling")
        if _contains_any(lowered, ["宿舍", "报修", "空调", "水管", "停电", "漏水", "维修"]):
            names.append("dormitory_repair")
        if _contains_any(lowered, ["校园卡", "饭卡", "挂失", "补卡"]):
            names.append("campus_card_loss")
        if _contains_any(lowered, ["奖学金", "助学金", "困难补助", "勤工助学"]):
            names.append("scholarship_application")
        return _dedupe(names)

    @staticmethod
    def emergency_response_prompt() -> str:
        return CampusFlowSkillLibrary.registry().get_required("campus_emergency_response").prompt_context()

    @staticmethod
    def staff_handoff_summary(report: CampusEventReport, user: UserAccount | None) -> str:
        template = CampusFlowSkillLibrary.registry().template_for("staff_handoff_summary")
        student = _student_label(user, report.user_id)
        urgency = "立即跟进" if report.risk_level == RiskLevel.HIGH.value else "按优先级跟进"
        next_steps = [
            f"{urgency}，核对事件地点、学生联系方式和现场是否仍存在危险。",
            f"将工单分派给 {report.department} 对应部门，并记录接手人员。",
            "紧急事件应同步校园保卫、宿管或相关值班人员，必要时联系当地紧急服务。",
            "将处置进展、完成结果和后续安排写入工单备注。",
        ]
        return _render_template(
            template,
            {
                "report_id": str(report.id),
                "student": student,
                "risk_level": report.risk_level,
                "category": report.category,
                "urgency": report.urgency,
                "department": report.department,
                "confidence": f"{report.confidence:.2f}",
                "summary": report.summary,
                "next_steps": "\n".join(f"- {step}" for step in next_steps),
                "content_excerpt": _truncate(report.content, 700),
            },
        )


def _split_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        raise SkillLoadError(f"{path} is missing YAML frontmatter")
    end = text.find("\n---", 4)
    if end == -1:
        raise SkillLoadError(f"{path} has unterminated YAML frontmatter")
    metadata = {}
    for line in text[4:end].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise SkillLoadError(f"{path} has invalid frontmatter line: {line}")
        key, value = stripped.split(":", 1)
        metadata[key.strip()] = value.strip().strip("\"'")
    return metadata, text[end + len("\n---") :].strip()


def _contains_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _render_template(template: str, values: dict[str, str]) -> str:
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def _student_label(user: UserAccount | None, user_id: int) -> str:
    if user is None:
        return f"userId={user_id}"
    if user.display_name:
        return f"{user.display_name} ({user.username})"
    return user.username


def _truncate(text: str, limit: int) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[:limit - 3]}..."
