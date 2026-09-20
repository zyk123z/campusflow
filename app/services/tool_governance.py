
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.enums import RiskLevel, ToolJobKind
from app.models.entities import CampusEventReport, ToolAuditRecord, ToolJob


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    description: str
    allowed_risks: tuple[str, ...]
    requires_report: bool = True


class ToolPolicyRegistry:
    POLICIES: dict[str, ToolPolicy] = {
        ToolJobKind.LEDGER_WRITE.value: ToolPolicy(
            name=ToolJobKind.LEDGER_WRITE.value,
            description="Write a campus event report into the service ledger.",
            allowed_risks=(RiskLevel.LOW.value, RiskLevel.MEDIUM.value, RiskLevel.HIGH.value),
        ),
        ToolJobKind.TICKET_CREATE.value: ToolPolicy(
            name=ToolJobKind.TICKET_CREATE.value,
            description="Create or reuse a service ticket for an actionable campus event.",
            allowed_risks=(RiskLevel.LOW.value, RiskLevel.MEDIUM.value, RiskLevel.HIGH.value),
        ),
        ToolJobKind.EMERGENCY_ALERT.value: ToolPolicy(
            name=ToolJobKind.EMERGENCY_ALERT.value,
            description="Send or log an urgent campus event alert for high risk reports.",
            allowed_risks=(RiskLevel.HIGH.value,),
        ),
    }

    @classmethod
    def policy_for(cls, tool_name: str) -> ToolPolicy | None:
        return cls.POLICIES.get(tool_name)

    @classmethod
    def authorize(cls, tool_name: str, report: CampusEventReport | None) -> tuple[bool, str, ToolPolicy | None]:
        policy = cls.policy_for(tool_name)
        if policy is None:
            return False, f"未知工具：{tool_name}", None
        if policy.requires_report and report is None:
            return False, "工具执行需要校园事件报告，但未找到 report", policy
        risk = report.risk_level if report is not None else ""
        if risk not in policy.allowed_risks:
            return False, f"工具 {tool_name} 不允许处理风险等级 {risk}", policy
        return True, "允许执行", policy


class ToolGovernanceService:
    def __init__(self, db: Session):
        self.db = db

    def start_job(self, job: ToolJob, report: CampusEventReport | None) -> ToolAuditRecord:
        allowed, reason, policy = ToolPolicyRegistry.authorize(job.kind, report)
        record = ToolAuditRecord(
            job_id=job.id,
            report_id=job.report_id,
            tool_name=job.kind,
            policy=policy.name if policy else "unknown",
            allowed=allowed,
            status="AUTHORIZED" if allowed else "BLOCKED",
            reason=reason,
            payload=_json(
                {
                    "jobId": job.id,
                    "kind": job.kind,
                    "attempts": job.attempts,
                    "riskLevel": report.risk_level if report is not None else None,
                    "policy": asdict(policy) if policy else None,
                }
            ),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def require_allowed(self, job: ToolJob, report: CampusEventReport | None) -> None:
        allowed, reason, _ = ToolPolicyRegistry.authorize(job.kind, report)
        if not allowed:
            raise RuntimeError(reason)

    def finish(self, record: ToolAuditRecord, status: str, reason: str = "", payload: dict[str, Any] | None = None) -> ToolAuditRecord:
        record.status = status
        record.reason = reason or record.reason
        if payload is not None:
            record.payload = _json(payload)
        record.updated_at = datetime.utcnow()
        self.db.add(record)
        self.db.commit()
        return record


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
