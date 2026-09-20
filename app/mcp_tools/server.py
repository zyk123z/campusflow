from app.core.bootstrap import create_schema
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.entities import CampusEventReport, ServiceTicket
from app.services.tools import ToolOrchestrationService

try:
    from mcp.server.fastmcp import FastMCP
except Exception as exc:  # pragma: no cover
    raise RuntimeError("请先安装 requirements.txt 中的 mcp 依赖") from exc


mcp = FastMCP("campusflow-python-tools")


@mcp.tool()
def campus_ledger_write(report_id: int) -> str:
    """Write one campus event report into the CampusFlow service ledger."""
    create_schema()
    db = SessionLocal()
    try:
        report = db.get(CampusEventReport, report_id)
        if report is None:
            return f"report {report_id} not found"
        record = ToolOrchestrationService(db, get_settings()).write_ledger(report)
        return f"success: {record.file_path}"
    finally:
        db.close()


@mcp.tool()
def campus_ticket_create(report_id: int) -> str:
    """Create or return the CampusFlow service ticket for one event report."""
    create_schema()
    db = SessionLocal()
    try:
        report = db.get(CampusEventReport, report_id)
        if report is None:
            return f"report {report_id} not found"
        ticket = ToolOrchestrationService(db, get_settings()).create_ticket(report)
        return f"success: ticketId={ticket.id}, reportId={ticket.report_id}, status={ticket.status}"
    finally:
        db.close()


@mcp.tool()
def campus_emergency_alert(ticket_id: int) -> str:
    """Send or record an urgent campus event alert for one service ticket."""
    create_schema()
    db = SessionLocal()
    try:
        ticket = db.get(ServiceTicket, ticket_id)
        if ticket is None:
            return f"ticket {ticket_id} not found"
        record = ToolOrchestrationService(db, get_settings()).send_ticket_alert(ticket)
        return f"{record.status}: ticketId={ticket_id}, {record.channel} -> {record.recipient}: {record.message}"
    finally:
        db.close()


@mcp.tool()
def campus_alert_ack(ticket_id: int, actor: str, note: str = "") -> str:
    """Mark a CampusFlow service ticket as acknowledged by campus staff."""
    create_schema()
    db = SessionLocal()
    try:
        ticket = ToolOrchestrationService(db, get_settings()).acknowledge_ticket(ticket_id, actor, note)
        return f"success: ticketId={ticket.id}, status={ticket.status}, acknowledgedBy={ticket.acknowledged_by}"
    except RuntimeError as exc:
        return str(exc)
    finally:
        db.close()


@mcp.tool()
def campus_ticket_note_add(ticket_id: int, actor: str, note: str) -> str:
    """Append a follow-up note to a CampusFlow service ticket."""
    create_schema()
    db = SessionLocal()
    try:
        record = ToolOrchestrationService(db, get_settings()).add_ticket_note(ticket_id, actor, note)
        return f"success: noteId={record.id}, ticketId={record.ticket_id}"
    except RuntimeError as exc:
        return str(exc)
    finally:
        db.close()


@mcp.tool()
def campus_staff_notify(report_id: int) -> str:
    """Send an urgent campus event notification for one event report."""
    create_schema()
    db = SessionLocal()
    try:
        report = db.get(CampusEventReport, report_id)
        if report is None:
            return f"report {report_id} not found"
        record = ToolOrchestrationService(db, get_settings()).notify(report)
        return f"{record.status}: {record.channel} -> {record.recipient}: {record.message}"
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
