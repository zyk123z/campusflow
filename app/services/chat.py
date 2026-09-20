from __future__ import annotations

import json
import logging

from sqlalchemy.orm import Session

from app.agents.harness import CampusFlowAgentHarness
from app.core.config import Settings
from app.models.entities import UserAccount
from app.schemas.dtos import ChatRequest, ChatStreamEvent
from app.services.ai import AiClient
from app.services.student_response import finalize_student_response, student_response_chunks


logger = logging.getLogger(__name__)


class ChatService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings
        self.ai = AiClient(settings)
        self.agent_harness = CampusFlowAgentHarness(db, settings)

    async def stream_chat(self, user: UserAccount, request: ChatRequest):
        outcome = self.agent_harness.run(user, request)
        yield sse("meta", ChatStreamEvent(type="meta", sessionId=outcome.session.public_id).model_dump(by_alias=True))
        assistant = []
        async for token in self.ai.stream(outcome.response_messages):
            assistant.append(token)
        student_response = finalize_student_response("".join(assistant), outcome.original_input)
        tool_feedback = ""
        try:
            dispatch = await self.agent_harness.dispatch_tools(outcome.tool_plan)
            tool_feedback = dispatch.student_message()
        except Exception as exc:
            logger.warning(
                "Post-response tool dispatch failed for session=%s report_id=%s: %s",
                outcome.session.public_id,
                outcome.report_id,
                exc,
                exc_info=True,
            )
            if outcome.tool_plan.requires_tools:
                tool_feedback = "你的事项已记录，但工单创建状态暂时无法确认。系统不会虚构工单编号，请稍后查看处理记录。"
        if tool_feedback:
            separator = "\n\n---\n\n" if student_response else ""
            appended = f"{separator}{tool_feedback}"
            student_response = f"{student_response}{appended}"
        for token in student_response_chunks(student_response):
            yield sse("token", ChatStreamEvent(type="token", sessionId=outcome.session.public_id, content=token).model_dump())
        if student_response:
            self.agent_harness.save_assistant_message(user, outcome.session, student_response)
        yield sse("done", ChatStreamEvent(type="done", sessionId=outcome.session.public_id).model_dump())


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"
