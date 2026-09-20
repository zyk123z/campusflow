from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import re
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable


class HarnessFailure(AssertionError):
    pass


@dataclass
class CheckResult:
    name: str
    passed: bool
    details: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


@dataclass
class HarnessContext:
    root: Path
    target_dir: Path
    settings: object
    database: object

    def session(self):
        return self.database.SessionLocal()


class InMemoryShortTermMemoryStore:
    _messages: dict[str, list[object]] = {}

    def __init__(self, settings):
        self.settings = settings

    def load_recent(self, session_public_id: str) -> list[object]:
        limit = self.settings.redis_memory_max_messages
        return list(self._messages.get(session_public_id, []))[-limit:]

    def messages_from_rows(self, rows: list[object]) -> list[object]:
        from app.schemas.dtos import AiMessage

        return [AiMessage(role=row.role.lower(), content=row.content) for row in rows]

    def append(self, session_public_id: str, role: str, content: str) -> None:
        from app.schemas.dtos import AiMessage
        from app.services.privacy import PrivacySanitizer

        values = self._messages.setdefault(session_public_id, [])
        values.append(AiMessage(role=role.lower(), content=PrivacySanitizer().sanitize(content)))
        del values[:-self.settings.redis_memory_max_messages]

    def replace(self, session_public_id: str, messages: list[object]) -> None:
        from app.schemas.dtos import AiMessage
        from app.services.privacy import PrivacySanitizer

        privacy = PrivacySanitizer()
        self._messages[session_public_id] = [
            AiMessage(role=message.role, content=privacy.sanitize(message.content))
            for message in list(messages)[-self.settings.redis_memory_max_messages:]
        ]

    @classmethod
    def reset(cls) -> None:
        cls._messages.clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run CampusFlow engineering harness checks.")
    parser.add_argument(
        "--suite",
        action="append",
        choices=["risk", "routing", "skills", "rag", "demo", "api", "tool-queue", "all"],
        default=None,
        help="Harness suite to run. Can be supplied multiple times.",
    )
    parser.add_argument("--json", action="store_true", help="Print only JSON output.")
    parser.add_argument(
        "--provider",
        choices=["mock", "ollama"],
        default="mock",
        help="AI provider used by answer-generating harness suites.",
    )
    args = parser.parse_args(argv)

    configure_environment(args.provider)
    context = build_context()
    install_harness_patches()
    reset_database(context)

    suites = resolve_suites(args.suite)
    results: list[CheckResult] = []
    for name, fn in suites:
        reset_database(context)
        InMemoryShortTermMemoryStore.reset()
        results.append(run_check(name, fn, context))

    report = write_report(context, results)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
    return 0 if all(result.passed for result in results) else 1


def configure_environment(ai_provider: str = "mock") -> None:
    root = Path(__file__).resolve().parents[2]
    target_dir = root / "target" / "harness"
    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = target_dir / "campusflow-harness.sqlite3"
    for suffix in ["", "-wal", "-shm"]:
        candidate = Path(f"{db_path}{suffix}")
        if candidate.exists():
            candidate.unlink()

    os.environ["DATABASE_URL"] = f"sqlite:///{db_path.as_posix()}"
    os.environ["AI_PROVIDER"] = ai_provider
    os.environ["AGENT_FRAMEWORK"] = "event_driven_multi_agent"
    os.environ["KNOWLEDGE_VECTOR_ENABLED"] = "false"
    os.environ["KNOWLEDGE_VECTOR_REQUIRED"] = "false"
    os.environ["TOOL_QUEUE_ENABLED"] = "false"
    os.environ["ALERT_EMAIL_DELIVERY_MODE"] = "log"
    os.environ["EXCEL_PATH"] = str((target_dir / "campusflow-service-ledger.xlsx").as_posix())
    os.environ["RAG_EVAL_OUTPUT"] = str((target_dir / "rag-eval-report.json").as_posix())


def build_context() -> HarnessContext:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import get_settings
    import app.core.database as database

    get_settings.cache_clear()
    settings = get_settings()
    if getattr(database, "engine", None) is not None:
        database.engine.dispose()
    database.engine = create_engine(settings.database_url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    database.SessionLocal = sessionmaker(bind=database.engine, autoflush=False, autocommit=False)
    return HarnessContext(
        root=Path(__file__).resolve().parents[2],
        target_dir=Path(__file__).resolve().parents[2] / "target" / "harness",
        settings=settings,
        database=database,
    )


def install_harness_patches() -> None:
    import app.agents.event_driven_runtime as runtime_module
    import app.agents.harness as harness_module
    import app.services.memory as memory_module

    harness_module.RedisShortTermMemoryStore = InMemoryShortTermMemoryStore
    memory_module.RedisShortTermMemoryStore = InMemoryShortTermMemoryStore
    runtime_module.RedisShortTermMemoryStore = InMemoryShortTermMemoryStore


def reset_database(context: HarnessContext) -> None:
    from app.core.bootstrap import seed_data

    context.database.Base.metadata.drop_all(bind=context.database.engine)
    context.database.Base.metadata.create_all(bind=context.database.engine)
    db = context.session()
    try:
        seed_data(db)
    finally:
        db.close()


def resolve_suites(requested: list[str] | None) -> list[tuple[str, Callable[[HarnessContext], dict]]]:
    all_suites: list[tuple[str, Callable[[HarnessContext], dict]]] = [
        ("Risk Safety Harness", run_risk_safety_harness),
        ("Agent Routing Harness", run_agent_routing_harness),
        ("Standard Skills Harness", run_standard_skills_harness),
        ("RAG Harness", run_rag_harness),
        ("Demo Scenarios Harness", run_demo_scenarios_harness),
        ("API Harness", run_api_harness),
        ("Tool Queue Harness", run_tool_queue_harness),
    ]
    if not requested or "all" in requested:
        return all_suites
    selected = set(requested)
    aliases = {
        "risk": "Risk Safety Harness",
        "routing": "Agent Routing Harness",
        "skills": "Standard Skills Harness",
        "rag": "RAG Harness",
        "demo": "Demo Scenarios Harness",
        "api": "API Harness",
        "tool-queue": "Tool Queue Harness",
    }
    names = {aliases[item] for item in selected}
    return [suite for suite in all_suites if suite[0] in names]


def run_check(name: str, fn: Callable[[HarnessContext], dict], context: HarnessContext) -> CheckResult:
    try:
        return CheckResult(name=name, passed=True, details=fn(context))
    except HarnessFailure as exc:
        return CheckResult(name=name, passed=False, failures=[str(exc)])
    except Exception as exc:
        return CheckResult(
            name=name,
            passed=False,
            failures=[f"{type(exc).__name__}: {exc}", traceback.format_exc()],
        )


def run_risk_safety_harness(context: HarnessContext) -> dict:
    from app.core.enums import RiskLevel, ToolJobKind
    from app.models.entities import CampusEventReport, ToolJob, UserAccount
    from app.schemas.dtos import ChatRequest
    from app.services.chat import ChatService

    context.settings.tool_queue_enabled = True
    db = context.session()
    observed = []
    try:
        user = db.query(UserAccount).filter(UserAccount.username == "student").one()
        cases = [
            {
                "id": "emergency-cn",
                "message": "宿舍插座正在冒烟，还有明显烧焦味。",
                "expects_report": True,
                "expects_risk": RiskLevel.HIGH.value,
                "expects_alert": True,
            },
            {
                "id": "emergency-en",
                "message": "There is a fire in the dormitory corridor and someone may be hurt.",
                "expects_report": True,
                "expects_risk": RiskLevel.HIGH.value,
                "expects_alert": True,
            },
            {
                "id": "service-low",
                "message": "宿舍空调坏了，我想提交报修。",
                "expects_report": True,
                "expects_alert": False,
            },
            {
                "id": "normal-chat",
                "message": "帮我解释一下 Python 字典推导式怎么写。",
                "expects_report": False,
                "expects_alert": False,
            },
        ]

        for case in cases:
            service = ChatService(db, context.settings)
            events, assistant = collect_chat_stream(service, user, ChatRequest(message=case["message"]))
            report = (
                db.query(CampusEventReport)
                .filter(CampusEventReport.content == case["message"])
                .order_by(CampusEventReport.id.desc())
                .first()
            )
            token_text = assistant.strip()
            expect(any(event["event"] == "meta" for event in events), f"{case['id']} did not emit meta event")
            expect(any(event["event"] == "done" for event in events), f"{case['id']} did not emit done event")
            expect(bool(token_text), f"{case['id']} did not stream assistant content")
            expect((report is not None) == case["expects_report"], f"{case['id']} report expectation failed")
            if report is not None:
                expected_risk = case.get("expects_risk")
                if expected_risk:
                    expect(report.risk_level == expected_risk, f"{case['id']} expected {expected_risk}, got {report.risk_level}")
                jobs = db.query(ToolJob).filter(ToolJob.report_id == report.id).all()
                has_alert = any(job.kind == ToolJobKind.EMERGENCY_ALERT.value for job in jobs)
                expect(has_alert == case["expects_alert"], f"{case['id']} alert job expectation failed")
                expect(
                    any(job.kind == ToolJobKind.LEDGER_WRITE.value for job in jobs),
                    f"{case['id']} did not enqueue ledger job",
                )
                expect(
                    any(job.kind == ToolJobKind.TICKET_CREATE.value for job in jobs),
                    f"{case['id']} did not enqueue ticket creation job",
                )
            forbidden = ["风险等级", "报告ID", "confidence", "FACILITY_HAZARD"]
            expect(not any(term in token_text for term in forbidden), f"{case['id']} exposed backend risk metadata")
            observed.append({"id": case["id"], "report": report is not None, "assistantChars": len(token_text)})
    finally:
        context.settings.tool_queue_enabled = False
        db.close()
    return {"cases": observed}


def run_agent_routing_harness(context: HarnessContext) -> dict:
    from app.agents.harness import CampusFlowAgentHarness
    from app.core.enums import IntentType, RiskLevel
    from app.models.entities import ChatSession, UserAccount
    from app.schemas.dtos import ChatRequest

    context.settings.agent_framework = "event_driven_multi_agent"
    db = context.session()
    observed = []
    try:
        user = db.query(UserAccount).filter(UserAccount.username == "student").one()
        cases = [
            {
                "id": "normal-companion",
                "message": "帮我解释一下 Python list comprehension。",
                "intent": IntentType.CHAT.value,
                "must_steps": ["UnderstandingAgent", "SafetyAgent", "ResponseAgent", "CoordinatorAgent"],
                "must_not_steps": ["ContextAgent"],
            },
            {
                "id": "policy-query",
                "message": "奖学金政策规定的资格条件和材料有哪些？",
                "intent": IntentType.POLICY_QUERY.value,
                "must_steps": ["UnderstandingAgent", "SafetyAgent", "ContextAgent", "ResponseAgent", "CoordinatorAgent"],
            },
            {
                "id": "service-request",
                "message": "宿舍空调坏了，我要提交报修。",
                "intent": IntentType.SERVICE_REQUEST.value,
                "must_steps": ["UnderstandingAgent", "SafetyAgent", "ContextAgent", "ResponseAgent", "CoordinatorAgent"],
            },
            {
                "id": "complaint",
                "message": "宿舍报修一直没人处理，我要投诉。",
                "intent": IntentType.COMPLAINT.value,
                "must_steps": ["UnderstandingAgent", "SafetyAgent", "ContextAgent", "ResponseAgent", "CoordinatorAgent"],
            },
            {
                "id": "campus-emergency",
                "message": "宿舍插座正在冒烟，还有烧焦味。",
                "intent": IntentType.EMERGENCY.value,
                "risk": RiskLevel.HIGH.value,
                "must_steps": ["UnderstandingAgent", "SafetyAgent", "ContextAgent", "ResponseAgent", "CoordinatorAgent"],
            },
        ]
        for case in cases:
            session = ChatSession(public_id=uuid.uuid4().hex, user_id=user.id, title=case["id"])
            db.add(session)
            db.commit()
            db.refresh(session)
            result = CampusFlowAgentHarness(db, context.settings).run(
                user,
                ChatRequest(message=case["message"], sessionId=session.public_id),
            )
            step_agents = [step.agent for step in result.agent_steps]
            expect(result.intent.value == case["intent"], f"{case['id']} expected intent {case['intent']}, got {result.intent.value}")
            if "risk" in case:
                expect(result.risk_level == case["risk"], f"{case['id']} expected risk {case['risk']}, got {result.risk_level}")
            for agent in case["must_steps"]:
                expect(agent in step_agents, f"{case['id']} did not run {agent}")
            for agent in case.get("must_not_steps", []):
                expect(agent not in step_agents, f"{case['id']} should not run {agent}")
            if case["intent"] != IntentType.CHAT.value:
                expect(len(result.retrieved_knowledge) > 0, f"{case['id']} retrieved no knowledge")
            else:
                expect(len(result.retrieved_knowledge) == 0, f"{case['id']} should not retrieve knowledge")
            observed.append({"id": case["id"], "intent": result.intent.value, "risk": result.risk_level, "steps": step_agents})
    finally:
        db.close()
    return {"cases": observed}


def run_standard_skills_harness(context: HarnessContext) -> dict:
    from app.core.enums import CampusDepartment, CampusEventCategory, IntentType, RiskLevel, UrgencyLevel
    from app.models.entities import CampusEventReport, UserAccount
    from app.services.skills import CampusFlowSkillLibrary

    expected = {
        "policy_answer_baseline",
        "campus_emergency_response",
        "complaint_handling",
        "dormitory_repair",
        "campus_card_loss",
        "scholarship_application",
        "staff_handoff_summary",
    }
    skills = CampusFlowSkillLibrary.list_skills()
    names = {skill.name for skill in skills}
    missing = sorted(expected - names)
    expect(not missing, f"missing standard skills: {missing}")

    statuses = CampusFlowSkillLibrary.status_items()
    failed = [item for item in statuses if item["status"] != "READY"]
    expect(not failed, f"standard skill load failures: {failed}")
    expect(all(Path(item["path"]).name == "SKILL.md" for item in statuses), "skill status did not expose SKILL.md paths")

    selected_names = CampusFlowSkillLibrary.response_skill_names(
        IntentType.SERVICE_REQUEST,
        RiskLevel.LOW,
        "宿舍空调坏了，我要提交报修。",
    )
    for name in [
        "policy_answer_baseline",
        "dormitory_repair",
    ]:
        expect(name in selected_names, f"service response did not select {name}")

    context_text = CampusFlowSkillLibrary.response_skill_context(
        IntentType.SERVICE_REQUEST,
        RiskLevel.LOW,
        "宿舍空调坏了，我要提交报修。",
    )
    expect("应用 skill: dormitory_repair" in context_text, "response context did not include standard skill body")

    high_risk_names = CampusFlowSkillLibrary.response_skill_names(
        IntentType.EMERGENCY,
        RiskLevel.HIGH,
        "宿舍插座正在冒烟。",
    )
    expect(high_risk_names == ["policy_answer_baseline", "campus_emergency_response"], "emergency skill selection changed")

    report = CampusEventReport(
        id=7,
        user_id=42,
        session_id=1,
        content="宿舍插座正在冒烟，还有烧焦味。",
        intent=IntentType.EMERGENCY.value,
        category=CampusEventCategory.FACILITY_HAZARD.value,
        urgency=UrgencyLevel.URGENT.value,
        department=CampusDepartment.LOGISTICS.value,
        risk_level=RiskLevel.HIGH.value,
        confidence=0.96,
        summary="检测到紧急校园设施安全事件",
    )
    user = UserAccount(
        id=42,
        username="student",
        display_name="测试学生",
        password_hash="unused",
        roles_csv="ROLE_USER",
    )
    handoff = CampusFlowSkillLibrary.staff_handoff_summary(report, user)
    for term in ["应用 skill: staff_handoff_summary", "报告ID：7", "测试学生 (student)", "立即跟进", "LOGISTICS"]:
        expect(term in handoff, f"handoff summary missing {term}")

    return {
        "skills": sorted(names),
        "selectedServiceSkills": selected_names,
        "selectedEmergencySkills": high_risk_names,
        "handoffChars": len(handoff),
    }


def run_rag_harness(context: HarnessContext) -> dict:
    from app.rag_eval.runner import evaluate_case
    from app.services.knowledge import KnowledgeService

    db = context.session()
    try:
        service = KnowledgeService(db, context.settings)
        dataset_path = context.root / context.settings.rag_eval_dataset
        cases = json.loads(dataset_path.read_text(encoding="utf-8"))
        results = [evaluate_case(service, case, context.settings.knowledge_top_k) for case in cases]
        total = max(1, len(results))
        hits = [item for item in results if item["hit"]]
        metrics = {
            "totalCases": len(results),
            "topK": context.settings.knowledge_top_k,
            "recallAtK": sum(item["recallAtK"] for item in results) / total,
            "precisionAtK": sum(item["precisionAtK"] for item in results) / total,
            "mrr": sum(item["reciprocalRank"] for item in results) / total,
            "ndcgAtK": sum(item["ndcgAtK"] for item in results) / total,
            "hitRate": len(hits) / total,
        }
        expect(metrics["totalCases"] >= 50, f"RAG dataset is too small: {metrics['totalCases']}")
        expect(metrics["hitRate"] >= 0.95, f"RAG hitRate below threshold: {metrics['hitRate']:.3f}")
        expect(metrics["recallAtK"] >= 0.95, f"RAG recallAtK below threshold: {metrics['recallAtK']:.3f}")
        expect(metrics["mrr"] >= 0.75, f"RAG MRR below threshold: {metrics['mrr']:.3f}")
        expect(metrics["ndcgAtK"] >= 0.75, f"RAG NDCG below threshold: {metrics['ndcgAtK']:.3f}")
        report = {"createdAt": datetime.utcnow().isoformat(), "metrics": metrics, "results": results}
        output = context.target_dir / "rag-eval-report.json"
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return metrics | {"report": str(output)}
    finally:
        db.close()


def run_demo_scenarios_harness(context: HarnessContext) -> dict:
    from app.models.entities import UserAccount
    from app.schemas.dtos import ChatRequest
    from app.services.chat import ChatService

    cases = [
        {
            "id": "dormitory-repair",
            "message": "宿舍空调不制冷，首次报修前我需要准备哪些信息？",
            "source": "dormitory-repair-guide.md",
            "terms": ["楼栋", "房间号", "故障设施", "可联系时段"],
            "expectsTicket": True,
            "expectedDepartment": "LOGISTICS",
        },
        {
            "id": "dormitory-repair-auto-ticket",
            "message": "楼栋号 B25，房间号 606，宿舍空调温度调节失效，昨天开始间歇性出现，影响宿舍区域，可联系时段为后天下午，请报修。",
            "source": "dormitory-repair-guide.md",
            "terms": ["自动", "内部", "工单", "无需", "重复提交"],
            "forbidden": ["登录校园服务平台"],
            "expectsTicket": True,
            "expectedDepartment": "LOGISTICS",
        },
        {
            "id": "financial-aid-differences",
            "message": "奖学金、助学金、困难补助和勤工助学有什么区别？我该怎么选？",
            "source": "scholarship-financial-aid.md",
            "terms": ["奖学金", "助学金", "困难补助", "勤工助学", "当年"],
            "forbidden": ["收入证明", "获奖证书", "个人简历", "科研成果", "社会活动参与度", "教务处", "学生资助管理中心"],
        },
        {
            "id": "campus-card-loss",
            "message": "我的校园卡刚丢，怎么先挂失再补办？如果有异常消费怎么处理？",
            "source": "campus-card-services.md",
            "terms": ["先挂失", "核对", "补办", "验证码"],
            "expectsTicket": True,
            "expectedDepartment": "IT_CENTER",
        },
        {
            "id": "repair-reminder-complaint",
            "message": "宿舍报修提交后一直没处理，我有原工单编号，应该先催办还是直接投诉？",
            "source": "complaint-and-appeal.md",
            "terms": ["催办", "原工单编号", "多次催办", "投诉"],
            "expectsTicket": True,
            "expectedDepartment": "STUDENT_AFFAIRS",
        },
    ]
    forbidden = [
        "LOGISTICS", "IT_CENTER", "risk", "confidence", "应用 skill", ".md",
        "我已经为你", "工单已创建", "已提交成功", "已通知工作人员",
    ]
    internal_codes = ["LOW", "MEDIUM", "HIGH", "ROUTINE", "PRIORITY", "URGENT"]

    context.settings.tool_queue_enabled = True
    db = context.session()
    observed = []
    try:
        user = db.query(UserAccount).filter(UserAccount.username == "student").one()
        for case in cases:
            events, assistant = collect_chat_stream(ChatService(db, context.settings), user, ChatRequest(message=case["message"]))
            response = assistant.strip()
            expect(response, f"{case['id']} returned an empty answer")
            for term in case["terms"]:
                expect(term in response, f"{case['id']} answer missing {term}: {response}")
            leaked = [term for term in [*forbidden, *case.get("forbidden", [])] if term.lower() in response.lower()]
            leaked.extend(code for code in internal_codes if re.search(rf"(?<![A-Z]){code}(?![A-Z])", response))
            expect(not leaked, f"{case['id']} exposed internal or false-success text: {leaked}")
            meta = next((event for event in events if event["event"] == "meta"), None)
            expect(meta is not None, f"{case['id']} did not emit session metadata")

            session_public_id = meta["data"]["sessionId"]
            from app.models.entities import AgentRunTrace, ChatSession
            session = db.query(ChatSession).filter(ChatSession.public_id == session_public_id).one()
            trace = (
                db.query(AgentRunTrace)
                .filter(AgentRunTrace.session_id == session.id)
                .order_by(AgentRunTrace.id.desc())
                .first()
            )
            expect(trace is not None, f"{case['id']} did not create an agent trace")
            if case.get("expectsTicket"):
                from app.models.entities import CampusEventReport, ServiceTicket
                report = db.query(CampusEventReport).filter(CampusEventReport.id == trace.report_id).one()
                ticket = db.query(ServiceTicket).filter(ServiceTicket.report_id == report.id).one()
                expect(report.department == case["expectedDepartment"], f"{case['id']} routed to {report.department}")
                expect(f"工单编号：{ticket.id}" in response, f"{case['id']} did not show the confirmed ticket id")
                expect("当前状态：待处理" in response, f"{case['id']} did not show student-facing ticket status")
            retrieved = json.loads(trace.retrieved_knowledge_json or "[]")
            sources = [item.get("source") for item in retrieved]
            expect(case["source"] in sources, f"{case['id']} did not retrieve {case['source']}: {sources}")
            observed.append({"id": case["id"], "source": case["source"], "answer": response})
    finally:
        context.settings.tool_queue_enabled = False
        db.close()
    return {"cases": observed}


def run_api_harness(context: HarnessContext) -> dict:
    from fastapi.testclient import TestClient

    from app.main import create_app

    context.settings.tool_queue_enabled = False
    app = create_app()
    student_auth = basic_auth("student", "student123")
    admin_auth = basic_auth("admin", "admin123")
    observed = {}
    with TestClient(app) as client:
        health = client.get("/actuator/health")
        expect(health.status_code == 200 and health.json()["status"] == "UP", "health endpoint failed")
        observed["health"] = health.json()

        profile = client.get("/api/profile", headers=student_auth)
        expect(profile.status_code == 200, f"student profile failed: {profile.status_code}")
        expect(profile.json()["username"] == "student", "student profile returned wrong user")

        agent_status = client.get("/api/agent/status", headers=student_auth)
        expect(agent_status.status_code == 200, f"agent status failed: {agent_status.status_code}")
        status_skills = agent_status.json()["skills"]
        expect(len(status_skills) >= 7, f"agent status exposed too few standard skills: {len(status_skills)}")
        expect(all(Path(skill["path"]).name == "SKILL.md" for skill in status_skills), "agent status did not expose standard skill paths")

        admin_chat = client.post("/api/chat/stream", headers=admin_auth, json={"message": "hello"})
        expect(admin_chat.status_code == 403, f"admin chat should be forbidden, got {admin_chat.status_code}")

        chat = client.post("/api/chat/stream", headers=student_auth, json={"message": "帮我解释一下 Python 函数。"})
        expect(chat.status_code == 200, f"student chat stream failed: {chat.status_code}")
        expect("event: meta" in chat.text and "event: done" in chat.text, "chat stream missing meta/done events")
        observed["chatStreamChars"] = len(chat.text)

        student_reports = client.get("/api/admin/reports", headers=student_auth)
        expect(student_reports.status_code == 403, f"student should not read admin reports: {student_reports.status_code}")

        admin_reports = client.get("/api/admin/reports", headers=admin_auth)
        expect(admin_reports.status_code == 200, f"admin reports failed: {admin_reports.status_code}")

        ingest = client.post(
            "/api/admin/knowledge",
            headers=admin_auth,
            json={"source": "harness-note", "content": "宿舍报修需要记录楼栋、房间、故障设施和可联系时段。"},
        )
        expect(ingest.status_code == 200, f"knowledge ingest failed: {ingest.status_code} {ingest.text}")
        expect(ingest.json()["chunks"] >= 1, "knowledge ingest did not create chunks")

        status = client.get("/api/admin/knowledge/status", headers=admin_auth)
        expect(status.status_code == 200, f"knowledge status failed: {status.status_code}")
        expect(status.json()["databaseChunks"] >= 1, "knowledge status returned no chunks")
        observed["knowledgeStatus"] = {
            "databaseChunks": status.json()["databaseChunks"],
            "vectorAvailable": status.json()["vectorAvailable"],
        }
    return observed


def run_tool_queue_harness(context: HarnessContext) -> dict:
    from app.core.enums import CampusDepartment, CampusEventCategory, IntentType, RiskLevel, TicketStatus, ToolJobKind, ToolJobStatus, ToolStatus, UrgencyLevel
    from app.models.entities import CampusEventReport, ToolJob, DeadLetterRecord, ChatSession, UserAccount
    from app.services.report import ReportService
    from app.services.tool_queue import RateLimiter, ToolQueueService, ToolQueueWorker
    from app.services.tools import ToolOrchestrationService

    context.settings.tool_queue_enabled = True
    db = context.session()
    worker = ToolQueueWorker(context.settings)
    try:
        user = db.query(UserAccount).filter(UserAccount.username == "student").one()
        session = ChatSession(public_id=uuid.uuid4().hex, user_id=user.id, title="tool-queue-harness")
        db.add(session)
        db.commit()
        db.refresh(session)
        report = CampusEventReport(
            user_id=user.id,
            session_id=session.id,
            content="宿舍插座正在冒烟，还有烧焦味。",
            intent=IntentType.EMERGENCY.value,
            category=CampusEventCategory.FACILITY_HAZARD.value,
            urgency=UrgencyLevel.URGENT.value,
            department=CampusDepartment.LOGISTICS.value,
            risk_level=RiskLevel.HIGH.value,
            confidence=0.96,
            summary="harness emergency campus event",
        )
        db.add(report)
        db.commit()
        db.refresh(report)

        jobs = ToolQueueService(db, context.settings).enqueue_report(report.id, report.risk_level)
        expect(len(jobs) == 3, f"expected 3 jobs for emergency report, got {len(jobs)}")
        ledger_job = next(job for job in jobs if job.kind == ToolJobKind.LEDGER_WRITE.value)
        ticket_job = next(job for job in jobs if job.kind == ToolJobKind.TICKET_CREATE.value)
        alert_job = next(job for job in jobs if job.kind == ToolJobKind.EMERGENCY_ALERT.value)
        expect(alert_job.depends_on_job_id == ticket_job.id, "alert job does not depend on ticket creation job")
        expect(not worker._dependency_ready(db, alert_job), "alert dependency should not be ready before ticket creation success")

        tools = ToolOrchestrationService(db, context.settings)
        ledger_record = tools.write_ledger(report)
        expect(ledger_record.status == ToolStatus.SUCCESS.value, f"ledger write failed: {ledger_record.message}")
        second_ledger_record = tools.write_ledger(report)
        expect(second_ledger_record.id == ledger_record.id, "ledger write is not idempotent")

        ticket_record = tools.create_ticket(report)
        second_ticket_record = tools.create_ticket(report)
        expect(second_ticket_record.id == ticket_record.id, "ticket creation is not idempotent")

        ticket_job.status = ToolJobStatus.SUCCESS.value
        db.add(ticket_job)
        db.commit()
        expect(worker._dependency_ready(db, alert_job), "alert dependency was not ready after ticket creation success")

        alert_record = tools.send_ticket_alert(ticket_record)
        expect(alert_record.status == ToolStatus.SUCCESS.value, f"alert notify failed: {alert_record.message}")
        db.refresh(ticket_record)
        expect(ticket_record.status == TicketStatus.ALERT_SENT.value, "ticket did not move to ALERT_SENT after alert")
        ticket_response = next(item for item in ReportService(db).service_tickets() if item.id == ticket_record.id)
        expect(ticket_response.department == CampusDepartment.LOGISTICS.value, "ticket department routing changed")
        expect(ticket_response.urgency == UrgencyLevel.URGENT.value, "ticket urgency changed")
        expect(ticket_response.slaMinutes == 5, "urgent ticket SLA is not five minutes")

        limiter = RateLimiter(1)
        first_allowed, _ = limiter.allow()
        second_allowed, retry_after = limiter.allow()
        expect(first_allowed, "rate limiter rejected first event")
        expect(not second_allowed and retry_after > 0, "rate limiter did not throttle second event")

        dead_job = ToolJob(
            report_id=report.id,
            kind=ToolJobKind.LEDGER_WRITE.value,
            status=ToolJobStatus.RUNNING.value,
            attempts=3,
            max_attempts=3,
        )
        db.add(dead_job)
        db.commit()
        db.refresh(dead_job)
        worker._fail_or_dead_letter(db, dead_job.id, RuntimeError("harness failure"))
        db.refresh(dead_job)
        dead_letter = db.query(DeadLetterRecord).filter(DeadLetterRecord.job_id == dead_job.id).first()
        expect(dead_job.status == ToolJobStatus.DEAD.value, "max-attempt job did not move to DEAD")
        expect(dead_letter is not None, "dead letter record was not created")

        return {
            "reportId": report.id,
            "ledgerJobId": ledger_job.id,
            "ticketJobId": ticket_job.id,
            "alertJobId": alert_job.id,
            "ticketId": ticket_record.id,
            "ticketSlaMinutes": ticket_response.slaMinutes,
            "ledgerPath": ledger_record.file_path,
            "deadLetterId": dead_letter.id,
        }
    finally:
        worker.stop()
        context.settings.tool_queue_enabled = False
        db.close()


def collect_chat_stream(service, user, request) -> tuple[list[dict], str]:
    async def collect() -> list[dict]:
        events = []
        async for chunk in service.stream_chat(user, request):
            events.extend(parse_sse(chunk))
        return events

    events = asyncio.run(collect())
    assistant = "".join(event["data"].get("content", "") for event in events if event["event"] == "token")
    return events, assistant


def parse_sse(chunk: str) -> list[dict]:
    events = []
    for block in chunk.strip().split("\n\n"):
        if not block:
            continue
        event_name = ""
        data = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event_name = line.removeprefix("event: ").strip()
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: ").strip())
        events.append({"event": event_name, "data": data})
    return events


def basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise HarnessFailure(message)


def write_report(context: HarnessContext, results: list[CheckResult]) -> dict:
    report = {
        "createdAt": datetime.utcnow().isoformat(),
        "environment": {
            "databaseUrl": context.settings.database_url,
            "aiProvider": context.settings.ai_provider,
            "agentFramework": context.settings.agent_framework,
            "knowledgeVectorEnabled": context.settings.knowledge_vector_enabled,
        },
        "passed": all(result.passed for result in results),
        "results": [
            {
                "name": result.name,
                "passed": result.passed,
                "details": result.details,
                "failures": result.failures,
            }
            for result in results
        ],
    }
    provider = str(getattr(context.settings, "ai_provider", "mock")).lower()
    output_name = "harness-report.json" if provider == "mock" else f"harness-report-{provider}.json"
    output = context.target_dir / output_name
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    report["reportPath"] = str(output)
    return report


def print_report(report: dict) -> None:
    print("CampusFlow Engineering Harness")
    print(f"Report: {report['reportPath']}")
    print("")
    for result in report["results"]:
        status = "PASS" if result["passed"] else "FAIL"
        print(f"[{status}] {result['name']}")
        if result["passed"] and result["details"]:
            compact = json.dumps(result["details"], ensure_ascii=False, default=str)
            print(f"       {compact[:900]}")
        for failure in result["failures"]:
            print(f"       {failure}")
    print("")
    print("Overall: PASS" if report["passed"] else "Overall: FAIL")


if __name__ == "__main__":
    sys.exit(main())
