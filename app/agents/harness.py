from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.factory import create_agent_runtime
from app.agents.result import AgentStep
from app.core.config import Settings
from app.core.enums import IntentType, MessageRole
from app.models.entities import CampusEventReport, ChatMessage, ChatSession, ServiceTicket, UserAccount
from app.schemas.dtos import AiMessage, ChatRequest
from app.services.assessment import CampusEventAssessment
from app.services.knowledge import SearchResult
from app.services.mcp_client import CampusFlowMcpToolClient
from app.services.memory import RedisShortTermMemoryStore
from app.services.privacy import PrivacySanitizer
from app.services.tool_queue import ToolQueueService
from app.services.tools import ToolOrchestrationService
from app.services.trace import AgentTraceService


logger = logging.getLogger(__name__)


@dataclass
class AgentToolPlan:
    report_id: int | None
    risk_level: str | None

    @property
    def requires_tools(self) -> bool:
        return self.report_id is not None


@dataclass
class AgentToolDispatchResult:
    state: str
    ticket_id: int | None = None
    ticket_status: str | None = None
    department: str | None = None

    def student_message(self) -> str:
        if self.state == "CONFIRMED" and self.ticket_id is not None:
            return (
                "已为你创建 CampusFlow 校园服务工单。\n\n"
                f"- 工单编号：{self.ticket_id}\n"
                f"- 当前状态：{student_ticket_status(self.ticket_status)}\n"
                f"- 负责部门：{student_department(self.department)}\n\n"
                "请保留该编号，无需就同一事项重复提交。"
            )
        if self.state == "QUEUED":
            return "你的事项已记录，工单正在后台创建。在显示真实工单编号前，系统不会声称已创建成功。"
        return ""


@dataclass
class AgentHarnessOutcome:
    session: ChatSession
    original_input: str
    model_input: str
    intent: IntentType
    risk_level: str | None
    assessment: CampusEventAssessment | None
    response_messages: list[AiMessage]
    agent_steps: list[AgentStep]
    retrieved_knowledge: list[SearchResult]
    report_id: int | None
    tool_plan: AgentToolPlan
    trace_id: int | None


class CampusFlowAgentHarness:
    """Runtime harness for one CampusFlow agent turn.

    The harness owns business orchestration around the agent runtime. HTTP/SSE
    code can stay thin while this class manages input preparation, persistence,
    risk report creation, tool planning, and trace data.
    """

    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.privacy = PrivacySanitizer()
        self.memory = RedisShortTermMemoryStore(settings)

    def run(self, user: UserAccount, request: ChatRequest) -> AgentHarnessOutcome:
        original_input = request.message.strip()
        model_input = self.privacy.sanitize(original_input)
        session = self._resolve_session(user, request.sessionId, original_input)
        agent_run = create_agent_runtime(self.db, self.settings).run(user, session, original_input, model_input)
        self.save_message(user, session, MessageRole.USER, original_input)

        report = self._create_report(user, session, original_input, agent_run)
        risk_level = report.risk_level if report is not None else None
        trace = AgentTraceService(self.db).save_run(
            user=user,
            session=session,
            original_input=original_input,
            sanitized_input=model_input,
            memory_brief=agent_run.memory_brief,
            agent_run=agent_run,
            report_id=report.id if report is not None else None,
        )
        tool_plan = AgentToolPlan(report_id=report.id if report is not None else None, risk_level=risk_level)
        return AgentHarnessOutcome(
            session=session,
            original_input=original_input,
            model_input=model_input,
            intent=agent_run.intent,
            risk_level=risk_level,
            assessment=agent_run.assessment,
            response_messages=agent_run.response_messages,
            agent_steps=agent_run.steps,
            retrieved_knowledge=agent_run.retrieved_knowledge,
            report_id=report.id if report is not None else None,
            tool_plan=tool_plan,
            trace_id=trace.id,
        )

    def save_assistant_message(self, user: UserAccount, session: ChatSession, content: str) -> None:
        self.save_message(user, session, MessageRole.ASSISTANT, content)

    async def dispatch_tools(self, tool_plan: AgentToolPlan) -> AgentToolDispatchResult:
        if tool_plan.report_id is None:
            return AgentToolDispatchResult("NOT_REQUIRED")
        report = self.db.get(CampusEventReport, tool_plan.report_id)
        if report is None:
            raise RuntimeError(f"report {tool_plan.report_id} not found")
        if self.settings.tool_queue_enabled:
            ToolQueueService(self.db, self.settings).enqueue_report(tool_plan.report_id, tool_plan.risk_level)
            try:
                ticket = ToolOrchestrationService(self.db, self.settings).create_ticket(report)
                return confirmed_dispatch(ticket, report.department)
            except Exception:
                logger.exception("Synchronous ticket confirmation failed for report=%s; queued jobs remain active", report.id)
                return AgentToolDispatchResult("QUEUED", department=report.department)
        await CampusFlowMcpToolClient(self.settings).handle_report(tool_plan.report_id, tool_plan.risk_level)
        self.db.expire_all()
        ticket = self.db.query(ServiceTicket).filter(ServiceTicket.report_id == report.id).first()
        return confirmed_dispatch(ticket, report.department) if ticket is not None else AgentToolDispatchResult("QUEUED", department=report.department)

    def save_message(self, user: UserAccount, session: ChatSession, role: MessageRole, content: str) -> None:
        self.db.add(ChatMessage(user_id=user.id, session_id=session.id, role=role.value, content=content))
        session.touch()
        self.db.add(session)
        self.db.commit()
        self.memory.append(session.public_id, role.value, content)

    def _resolve_session(self, user: UserAccount, public_id: str | None, text: str) -> ChatSession:
        if public_id:
            session = self.db.query(ChatSession).filter(ChatSession.public_id == public_id, ChatSession.user_id == user.id).first()
            if session is None:
                raise ValueError("Session not found")
            return session
        session = ChatSession(public_id=uuid.uuid4().hex, user_id=user.id, title=text[:36])
        self.db.add(session)
        self.db.commit()
        self.db.refresh(session)
        return session

    def _create_report(self, user: UserAccount, session: ChatSession, text: str, agent_run) -> CampusEventReport | None:
        if not agent_run.requires_report or agent_run.assessment is None:
            return None
        report = CampusEventReport(
            user_id=user.id,
            session_id=session.id,
            content=text,
            intent=agent_run.intent.value,
            category=agent_run.assessment.category.value,
            urgency=agent_run.assessment.urgency.value,
            department=agent_run.assessment.department.value,
            risk_level=agent_run.assessment.risk.value,
            confidence=agent_run.assessment.confidence,
            summary=agent_run.assessment.summary,
        )
        self.db.add(report)
        self.db.commit()
        self.db.refresh(report)
        return report


def confirmed_dispatch(ticket: ServiceTicket, department: str) -> AgentToolDispatchResult:
    return AgentToolDispatchResult(
        state="CONFIRMED",
        ticket_id=ticket.id,
        ticket_status=ticket.status,
        department=department,
    )


def student_ticket_status(status: str | None) -> str:
    return {
        "OPEN": "待处理",
        "ALERT_SENT": "已通知值班人员",
        "ACKNOWLEDGED": "已接手",
        "RESOLVED": "已完成",
    }.get(str(status or "").upper(), "已记录")


def student_department(department: str | None) -> str:
    return {
        "SERVICE_CENTER": "一站式服务中心",
        "ACADEMIC_AFFAIRS": "教务部门",
        "STUDENT_AFFAIRS": "学生事务部门",
        "LOGISTICS": "后勤部门",
        "SECURITY": "校园保卫部门",
        "FINANCE": "财务部门",
        "IT_CENTER": "信息服务部门",
    }.get(str(department or "").upper(), "对应校内部门")
