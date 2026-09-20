from __future__ import annotations

from datetime import datetime, timedelta

from app.core.enums import TicketStatus, UrgencyLevel


SLA_MINUTES = {
    UrgencyLevel.URGENT.value: 5,
    UrgencyLevel.PRIORITY.value: 240,
    UrgencyLevel.ROUTINE.value: 1440,
}


def sla_minutes_for(urgency: str) -> int:
    return SLA_MINUTES.get(str(urgency).upper(), SLA_MINUTES[UrgencyLevel.ROUTINE.value])


def sla_deadline(created_at: datetime, urgency: str) -> datetime:
    return created_at + timedelta(minutes=sla_minutes_for(urgency))


def sla_status(deadline: datetime, ticket_status: str, now: datetime | None = None) -> str:
    if ticket_status in {TicketStatus.ACKNOWLEDGED.value, TicketStatus.RESOLVED.value}:
        return "COMPLETED"
    return "OVERDUE" if (now or datetime.utcnow()) > deadline else "ACTIVE"
