from __future__ import annotations

import json
from dataclasses import dataclass

from app.core.enums import CampusDepartment, CampusEventCategory, RiskLevel, UrgencyLevel
from app.schemas.dtos import AiMessage
from app.services.ai import AiClient, PromptTemplates, has_emergency_signal


@dataclass
class CampusEventAssessment:
    category: CampusEventCategory
    urgency: UrgencyLevel
    department: CampusDepartment
    risk: RiskLevel
    confidence: float
    summary: str


class CampusEventAssessmentService:
    def __init__(self, ai: AiClient):
        self.ai = ai

    def assess(self, text: str, history: list[AiMessage] | None = None) -> CampusEventAssessment:
        if has_emergency_signal(text):
            return emergency_assessment(text)
        try:
            raw = self.ai.complete(PromptTemplates.campus_event_prompt(history or [], text))
            start = raw.find("{")
            end = raw.rfind("}")
            data = json.loads(raw[start:end + 1] if start >= 0 and end > start else raw)
            category = CampusEventCategory(str(data.get("category", "GENERAL")).upper())
            urgency = UrgencyLevel(str(data.get("urgency", "ROUTINE")).upper())
            department = CampusDepartment(str(data.get("department", department_for(category).value)).upper())
            risk = RiskLevel(str(data.get("risk", risk_for_urgency(urgency).value)).upper())
            confidence = max(0.0, min(1.0, float(data.get("confidence", 0.75))))
            inferred_risk = risk_for_urgency(urgency)
            if risk_order(inferred_risk) > risk_order(risk):
                risk = inferred_risk
            assessment = CampusEventAssessment(category, urgency, department, risk, confidence, str(data.get("summary", "校园事务评估结果")))
            return apply_domain_routing(text, assessment)
        except Exception:
            return heuristic(text)


def emergency_assessment(text: str) -> CampusEventAssessment:
    lowered = text.lower()
    security_words = ["打架", "持刀", "尾随", "威胁", "被困", "失联", "暴力", "袭击"]
    category = CampusEventCategory.CAMPUS_SECURITY if any(word in lowered for word in security_words) else CampusEventCategory.FACILITY_HAZARD
    department = CampusDepartment.SECURITY if category == CampusEventCategory.CAMPUS_SECURITY else CampusDepartment.LOGISTICS
    return CampusEventAssessment(category, UrgencyLevel.URGENT, department, RiskLevel.HIGH, 0.96, "检测到需要立即处置的校园安全事件")


def heuristic(text: str) -> CampusEventAssessment:
    lowered = text.lower()
    if any(word in lowered for word in ["投诉", "举报", "一直没人处理", "服务态度", "不合理"]):
        return CampusEventAssessment(CampusEventCategory.COMPLAINT, UrgencyLevel.PRIORITY, CampusDepartment.STUDENT_AFFAIRS, RiskLevel.MEDIUM, 0.78, "检测到投诉或升级处理诉求")
    if any(word in lowered for word in ["宿舍", "空调", "水管", "停电", "漏水", "报修"]):
        return CampusEventAssessment(CampusEventCategory.DORMITORY, UrgencyLevel.ROUTINE, CampusDepartment.LOGISTICS, RiskLevel.LOW, 0.76, "检测到宿舍或后勤事务")
    if any(word in lowered for word in ["校园卡", "饭卡", "挂失", "补卡"]):
        return CampusEventAssessment(CampusEventCategory.CAMPUS_CARD, UrgencyLevel.PRIORITY, CampusDepartment.IT_CENTER, RiskLevel.MEDIUM, 0.8, "检测到校园卡服务需求")
    if any(word in lowered for word in ["奖学金", "助学金", "困难补助", "勤工助学"]):
        return CampusEventAssessment(CampusEventCategory.FINANCIAL_AID, UrgencyLevel.ROUTINE, CampusDepartment.STUDENT_AFFAIRS, RiskLevel.LOW, 0.78, "检测到奖助学金事务")
    if any(word in lowered for word in ["选课", "成绩", "考试", "请假", "学籍", "证明"]):
        return CampusEventAssessment(CampusEventCategory.ACADEMIC_AFFAIRS, UrgencyLevel.ROUTINE, CampusDepartment.ACADEMIC_AFFAIRS, RiskLevel.LOW, 0.76, "检测到教务事务")
    return CampusEventAssessment(CampusEventCategory.GENERAL, UrgencyLevel.ROUTINE, CampusDepartment.SERVICE_CENTER, RiskLevel.LOW, 0.66, "未检测到紧急校园事件")


def apply_domain_routing(text: str, assessment: CampusEventAssessment) -> CampusEventAssessment:
    """Keep LLM risk analysis while enforcing deterministic routing for known campus domains."""
    routed = heuristic(text)
    if routed.category == CampusEventCategory.GENERAL:
        return assessment
    urgency = assessment.urgency
    risk = assessment.risk
    if urgency_order(routed.urgency) > urgency_order(urgency):
        urgency = routed.urgency
    if risk_order(routed.risk) > risk_order(risk):
        risk = routed.risk
    return CampusEventAssessment(
        routed.category,
        urgency,
        routed.department,
        risk,
        max(assessment.confidence, routed.confidence),
        routed.summary,
    )


def department_for(category: CampusEventCategory) -> CampusDepartment:
    return {
        CampusEventCategory.ACADEMIC_AFFAIRS: CampusDepartment.ACADEMIC_AFFAIRS,
        CampusEventCategory.STUDENT_AFFAIRS: CampusDepartment.STUDENT_AFFAIRS,
        CampusEventCategory.DORMITORY: CampusDepartment.LOGISTICS,
        CampusEventCategory.CAMPUS_CARD: CampusDepartment.IT_CENTER,
        CampusEventCategory.FINANCIAL_AID: CampusDepartment.STUDENT_AFFAIRS,
        CampusEventCategory.FACILITY_HAZARD: CampusDepartment.LOGISTICS,
        CampusEventCategory.CAMPUS_SECURITY: CampusDepartment.SECURITY,
        CampusEventCategory.COMPLAINT: CampusDepartment.STUDENT_AFFAIRS,
        CampusEventCategory.GENERAL: CampusDepartment.SERVICE_CENTER,
    }[category]


def risk_for_urgency(urgency: UrgencyLevel) -> RiskLevel:
    return {
        UrgencyLevel.ROUTINE: RiskLevel.LOW,
        UrgencyLevel.PRIORITY: RiskLevel.MEDIUM,
        UrgencyLevel.URGENT: RiskLevel.HIGH,
    }[urgency]


def risk_order(risk: RiskLevel) -> int:
    return {RiskLevel.LOW: 1, RiskLevel.MEDIUM: 2, RiskLevel.HIGH: 3}[risk]


def urgency_order(urgency: UrgencyLevel) -> int:
    return {UrgencyLevel.ROUTINE: 1, UrgencyLevel.PRIORITY: 2, UrgencyLevel.URGENT: 3}[urgency]
