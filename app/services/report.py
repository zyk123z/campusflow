from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import AgentRunTrace, CampusAlertRecord, CampusEventReport, ChatMessage, ChatSession, DeadLetterRecord, LedgerRecord, ServiceTicket, TicketNote, ToolAuditRecord, ToolJob, UserAccount
from app.schemas.dtos import AgentRunTraceResponse, ConversationMessageResponse, ConversationResponse, DeadLetterResponse, ReportResponse, ServiceTicketResponse, TicketNoteResponse, ToolAuditResponse, ToolJobResponse, ToolRecordResponse
from app.services.sla import sla_deadline, sla_minutes_for, sla_status


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def latest_reports(self, user_id: int | None = None) -> list[ReportResponse]:
        query = self.db.query(CampusEventReport).order_by(CampusEventReport.created_at.desc())
        if user_id is not None:
            query = query.filter(CampusEventReport.user_id == user_id)
        return [self._report_response(item) for item in query.limit(100).all()]

    def ledger_records(self) -> list[ToolRecordResponse]:
        rows = self.db.query(LedgerRecord).order_by(LedgerRecord.created_at.desc()).limit(100).all()
        return [
            ToolRecordResponse(id=row.id, reportId=row.report_id, status=row.status, message=row.message, createdAt=row.created_at, filePath=row.file_path)
            for row in rows
        ]

    def excel_records(self) -> list[ToolRecordResponse]:
        """Compatibility alias for clients created before the CampusFlow rename."""
        return self.ledger_records()

    def alert_records(self) -> list[ToolRecordResponse]:
        rows = self.db.query(CampusAlertRecord).order_by(CampusAlertRecord.created_at.desc()).limit(100).all()
        return [
            ToolRecordResponse(
                id=row.id,
                reportId=row.report_id,
                status=row.status,
                message=row.message,
                createdAt=row.created_at,
                channel=row.channel,
                recipient=row.recipient,
            )
            for row in rows
        ]

    def service_tickets(self) -> list[ServiceTicketResponse]:
        rows = self.db.query(ServiceTicket).order_by(ServiceTicket.updated_at.desc()).limit(100).all()
        report_ids = [row.report_id for row in rows]
        reports = {
            report.id: report
            for report in self.db.query(CampusEventReport).filter(CampusEventReport.id.in_(report_ids)).all()
        } if report_ids else {}
        return [
            ServiceTicketResponse(
                id=row.id,
                reportId=row.report_id,
                category=reports[row.report_id].category,
                urgency=reports[row.report_id].urgency,
                department=reports[row.report_id].department,
                riskLevel=row.risk_level,
                status=row.status,
                slaMinutes=sla_minutes_for(reports[row.report_id].urgency),
                slaDeadline=sla_deadline(row.created_at, reports[row.report_id].urgency),
                slaStatus=sla_status(sla_deadline(row.created_at, reports[row.report_id].urgency), row.status),
                owner=row.owner,
                summary=row.summary,
                handoffSummary=row.handoff_summary,
                acknowledgedBy=row.acknowledged_by,
                acknowledgedAt=row.acknowledged_at,
                createdAt=row.created_at,
                updatedAt=row.updated_at,
            )
            for row in rows
            if row.report_id in reports
        ]

    def ticket_notes(self, ticket_id: int) -> list[TicketNoteResponse]:
        rows = self.db.query(TicketNote).filter(TicketNote.ticket_id == ticket_id).order_by(TicketNote.created_at.asc()).all()
        return [
            TicketNoteResponse(id=row.id, ticketId=row.ticket_id, actor=row.actor, note=row.note, createdAt=row.created_at)
            for row in rows
        ]

    def tool_jobs(self) -> list[ToolJobResponse]:
        rows = self.db.query(ToolJob).order_by(ToolJob.created_at.desc()).limit(100).all()
        return [
            ToolJobResponse(
                id=row.id,
                reportId=row.report_id,
                kind=row.kind,
                status=row.status,
                attempts=row.attempts,
                maxAttempts=row.max_attempts,
                dependsOnJobId=row.depends_on_job_id,
                runAfter=row.run_after,
                lastError=row.last_error,
                createdAt=row.created_at,
                updatedAt=row.updated_at,
            )
            for row in rows
        ]

    def dead_letters(self) -> list[DeadLetterResponse]:
        rows = self.db.query(DeadLetterRecord).order_by(DeadLetterRecord.created_at.desc()).limit(100).all()
        return [
            DeadLetterResponse(
                id=row.id,
                jobId=row.job_id,
                reportId=row.report_id,
                kind=row.kind,
                reason=row.reason,
                payload=row.payload,
                createdAt=row.created_at,
            )
            for row in rows
        ]


    def agent_run_traces(self) -> list[AgentRunTraceResponse]:
        rows = self.db.query(AgentRunTrace).order_by(AgentRunTrace.created_at.desc()).limit(100).all()
        responses = []
        for row in rows:
            user = self.db.get(UserAccount, row.user_id)
            session = self.db.get(ChatSession, row.session_id)
            responses.append(
                AgentRunTraceResponse(
                    id=row.id,
                    sessionId=session.public_id if session else "",
                    reportId=row.report_id,
                    username=user.username if user else "",
                    intent=row.intent,
                    riskLevel=row.risk_level,
                    originalInput=row.original_input,
                    sanitizedInput=row.sanitized_input,
                    memoryBrief=row.memory_brief,
                    agentSteps=_loads(row.agent_steps_json, []),
                    retrievedKnowledge=_loads(row.retrieved_knowledge_json, []),
                    responseMessages=_loads(row.response_messages_json, []),
                    assessment=_loads(row.assessment_json, {}),
                    createdAt=row.created_at,
                )
            )
        return responses

    def tool_audits(self) -> list[ToolAuditResponse]:
        rows = self.db.query(ToolAuditRecord).order_by(ToolAuditRecord.created_at.desc()).limit(100).all()
        return [
            ToolAuditResponse(
                id=row.id,
                jobId=row.job_id,
                reportId=row.report_id,
                toolName=row.tool_name,
                policy=row.policy,
                allowed=row.allowed,
                status=row.status,
                reason=row.reason,
                payload=_loads(row.payload, {}),
                createdAt=row.created_at,
                updatedAt=row.updated_at,
            )
            for row in rows
        ]

    def conversation(self, public_id: str) -> ConversationResponse:
        session = self.db.query(ChatSession).filter(ChatSession.public_id == public_id).first()
        if session is None:
            raise ValueError("Session not found")
        rows = self.db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.asc()).all()
        return ConversationResponse(
            sessionId=session.public_id,
            title=session.title,
            messages=[ConversationMessageResponse(role=row.role, content=row.content, createdAt=row.created_at) for row in rows],
        )

    def _report_response(self, report: CampusEventReport) -> ReportResponse:
        user = self.db.get(UserAccount, report.user_id)
        session = self.db.get(ChatSession, report.session_id)
        return ReportResponse(
            id=report.id,
            sessionId=session.public_id if session else "",
            username=user.username if user else "",
            displayName=user.display_name if user else "",
            content=report.content,
            intent=report.intent,
            category=report.category,
            urgency=report.urgency,
            department=report.department,
            riskLevel=report.risk_level,
            confidence=report.confidence,
            summary=report.summary,
            createdAt=report.created_at,
        )


def _loads(raw: str, default):
    import json

    try:
        return json.loads(raw or "")
    except Exception:
        return default
