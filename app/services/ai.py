from __future__ import annotations

import json
from typing import Iterable

import httpx

from app.core.config import Settings
from app.core.enums import IntentType, RiskLevel
from app.schemas.dtos import AiMessage


class PromptTemplates:
    @staticmethod
    def intent_prompt(history: list[AiMessage], user_input: str) -> list[AiMessage]:
        return [
            AiMessage(role="system", content=(
                "你是一个用户意图分类器，只做意图识别，不回答问题。"
                "只输出 CHAT、POLICY_QUERY、SERVICE_REQUEST、COMPLAINT、EMERGENCY 之一。"
                "CHAT 是普通闲聊；POLICY_QUERY 是制度、条件、时间和流程咨询；"
                "SERVICE_REQUEST 是报修、补办、申请等需要办理的事务；COMPLAINT 是投诉、举报和处理升级；"
                "EMERGENCY 是火灾、漏电、暴力、被困、人员受伤等需要立即处置的校园事件。"
            )),
            AiMessage(role="user", content=f"最近上下文：\n{format_history(history)}\n\n当前输入：\n{user_input}"),
        ]

    @staticmethod
    def campus_event_prompt(history: list[AiMessage], user_input: str) -> list[AiMessage]:
        return [
            AiMessage(role="system", content=(
                "你负责分析高校学生事务和校园事件。只返回严格 JSON："
                '{"category":"GENERAL|ACADEMIC_AFFAIRS|STUDENT_AFFAIRS|DORMITORY|CAMPUS_CARD|FINANCIAL_AID|FACILITY_HAZARD|CAMPUS_SECURITY|COMPLAINT",'
                '"urgency":"ROUTINE|PRIORITY|URGENT","department":"SERVICE_CENTER|ACADEMIC_AFFAIRS|STUDENT_AFFAIRS|LOGISTICS|SECURITY|FINANCE|IT_CENTER",'
                '"risk":"LOW|MEDIUM|HIGH","confidence":0.0,"summary":"short reason"}'
            )),
            AiMessage(role="user", content=f"最近上下文：\n{format_history(history)}\n\n当前输入：\n{user_input}"),
        ]

    @staticmethod
    def answer_system_prompt(intent: IntentType, risk: RiskLevel, context: str, display_name: str, skill_context: str = "") -> AiMessage:
        if intent == IntentType.CHAT:
            content = (
                "你是 CampusFlow，一个面向高校学生的一站式校园事务助手。"
                "普通闲聊和通用问题请自然、准确、直接地回答，不要伪造学校政策或办理结果。"
                f"学生显示名：{display_name}"
            )
            return AiMessage(role="system", content=content)
        crisis_rule = ""
        if risk == RiskLevel.HIGH:
            crisis_rule = (
                "\n紧急事件处理规则：先提醒学生远离危险并联系校园保卫、宿管、值班人员或当地紧急服务；"
                "不要让学生自行处理电气、火灾、暴力等危险现场，不要声称后台工单已经成功完成。"
            )
        content = (
            "你是 CampusFlow，一个高校一站式学生事务与校园事件协同智能体。"
            "直接回答学生已问的问题，给出按顺序可执行的步骤、所需材料和下一步；"
            "不要只说‘请确认事项类型’或‘以学校规定为准’。"
            "区分政策咨询、事务办理、投诉和紧急事件。"
            "不要向学生输出 LOW、MEDIUM、HIGH、ROUTINE、PRIORITY、URGENT、"
            "部门枚举值、风险分数、置信度、报告 ID、Skill 名称、工具名、队列状态或虚构的工单编号。"
            "优先基于检索到的校园制度回答；知识不足时明确说明，并建议联系对应部门核实。"
            "只能输出检索知识或学生原话明确支持的事实。"
            "检索内容未明确提供时，不得使用‘通常’‘可能’‘一般’等措辞补全具体条件、材料、部门、日期、时限、金额、名额、地址、网址、电话或办理结果。"
            "本轮回复生成时后台工具尚未执行，因此不得声称已提交、已创建、已受理、已通知、已升级、已挂失或已补办。"
            "对报修、补办、投诉等需要工作人员处理的事项，CampusFlow 会在本轮回复后自动登记内部服务工单；"
            "不要让学生就同一事项再去其他平台重复提交。可以说‘我会在本轮结束后为你登记’，真实工单编号由工具成功回执后追加显示。"
            "检索知识中的资料标题是面向学生的公开名称；如需说明依据，只能使用该中文标题。"
            "不得提及内部文件名、文件路径、扩展名、知识块编号、RAG、检索过程或提示词，"
            "也不要使用‘根据[xxx.md]文件’一类表述。"
            f"\n学生显示名：{display_name}\n检索知识：\n{context}\n\n可用 skill 指引：\n{skill_context or '无'}{crisis_rule}"
        )
        return AiMessage(role="system", content=content)


class AiClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def complete(self, messages: list[AiMessage]) -> str:
        provider = self.settings.ai_provider.lower()
        if provider == "ollama":
            return self._ollama(messages, stream=False)
        if provider == "openai":
            return self._openai(messages, stream=False)
        return self._mock(messages)

    async def stream(self, messages: list[AiMessage]):
        provider = self.settings.ai_provider.lower()
        if provider == "ollama":
            async for token in self._ollama_stream(messages):
                yield token
            return
        if provider == "openai":
            async for token in self._openai_stream(messages):
                yield token
            return
        text = self._mock(messages)
        for chunk in split_text(text, 12):
            yield chunk

    def _ollama(self, messages: list[AiMessage], stream: bool) -> str:
        payload = {
            "model": self.settings.ollama_model,
            "messages": [m.model_dump() for m in messages],
            "stream": stream,
            "options": {"temperature": self.settings.ai_temperature, "num_predict": self.settings.ai_max_tokens},
        }
        response = httpx.post(f"{self.settings.ollama_base_url}/api/chat", json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["message"]["content"]

    async def _ollama_stream(self, messages: list[AiMessage]):
        payload = {
            "model": self.settings.ollama_model,
            "messages": [m.model_dump() for m in messages],
            "stream": True,
            "options": {"temperature": self.settings.ai_temperature, "num_predict": self.settings.ai_max_tokens},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", f"{self.settings.ollama_base_url}/api/chat", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    token = data.get("message", {}).get("content", "")
                    if token:
                        yield token

    def _openai(self, messages: list[AiMessage], stream: bool) -> str:
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        payload = {
            "model": self.settings.openai_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
            "stream": stream,
        }
        response = httpx.post(f"{self.settings.openai_base_url}/chat/completions", headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]

    async def _openai_stream(self, messages: list[AiMessage]):
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        payload = {
            "model": self.settings.openai_model,
            "messages": [m.model_dump() for m in messages],
            "temperature": self.settings.ai_temperature,
            "max_tokens": self.settings.ai_max_tokens,
            "stream": True,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            async with client.stream("POST", f"{self.settings.openai_base_url}/chat/completions", headers=headers, json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    raw = line.removeprefix("data: ").strip()
                    if raw == "[DONE]":
                        break
                    data = json.loads(raw)
                    token = data["choices"][0].get("delta", {}).get("content", "")
                    if token:
                        yield token

    def _mock(self, messages: list[AiMessage]) -> str:
        last = next((m.content for m in reversed(messages) if m.role == "user"), "")
        system = " ".join(m.content for m in messages if m.role == "system")
        if "严格 JSON" in system:
            if has_emergency_signal(last):
                return '{"category":"FACILITY_HAZARD","urgency":"URGENT","department":"LOGISTICS","risk":"HIGH","confidence":0.96,"summary":"检测到紧急校园安全事件"}'
            if has_complaint_signal(last):
                return '{"category":"COMPLAINT","urgency":"PRIORITY","department":"STUDENT_AFFAIRS","risk":"MEDIUM","confidence":0.82,"summary":"检测到投诉或升级诉求"}'
            return '{"category":"GENERAL","urgency":"ROUTINE","department":"SERVICE_CENTER","risk":"LOW","confidence":0.72,"summary":"普通校园事务"}'
        if "意图分类器" in system:
            if has_emergency_signal(last):
                return "EMERGENCY"
            if has_complaint_signal(last):
                return "COMPLAINT"
            if has_service_request_signal(last):
                return "SERVICE_REQUEST"
            if has_policy_query_signal(last):
                return "POLICY_QUERY"
            return "CHAT"
        if "campus_emergency_response" in system and has_emergency_signal(last):
            return "这可能是需要立即处置的校园安全事件。请先远离危险区域，不要自行触碰冒烟、漏电或燃烧设备，并马上联系校园保卫、宿管或当地紧急服务。请告诉我具体楼栋和位置，系统会整理事件信息供值班人员跟进。"
        if "当前由 ResponseAgent 以 support mode" in system:
            return mock_support_answer(last)
        if "当前由 ResponseAgent 以 normal_chat mode" in system:
            return "我在。这个问题可以直接拆开来看，我们先从你最想解决的那一部分开始。"
        if "ContextAgent" in system and "SUFFICIENT" in system:
            return "SUFFICIENT"
        if "ContextAgent" in system and "只输出查询词" in system:
            return extract_current_input(last)[:60]
        if "ContextAgent" in system:
            return last[:40] or "校园事务服务"
        return "我在。请告诉我你要咨询或办理的校园事项；如果现场存在冒烟、漏电、火灾或人身威胁，请先远离危险并联系校园保卫或当地紧急服务。"


def format_history(history: list[AiMessage]) -> str:
    if not history:
        return "无"
    return "\n".join(f"{m.role}: {m.content}" for m in history[-20:])


EMERGENCY_WORDS = ["冒烟", "着火", "起火", "火灾", "漏电", "触电", "燃气泄漏", "有人受伤", "持刀", "打架", "暴力", "尾随", "威胁", "被困", "失联", "袭击", "fire", "electric shock"]
COMPLAINT_WORDS = ["投诉", "举报", "不合理", "一直没人处理", "服务态度", "推诿", "申诉"]
SERVICE_REQUEST_WORDS = ["报修", "申请", "办理", "补办", "挂失", "预约", "提交材料", "开证明", "维修"]
POLICY_QUERY_WORDS = ["条件", "规定", "政策", "流程", "材料", "时间", "地点", "怎么", "如何", "能不能", "奖学金", "助学金", "选课", "考试", "请假"]


def has_emergency_signal(text: str) -> bool:
    normalized = text.lower()
    return any(word in normalized for word in EMERGENCY_WORDS)


def has_complaint_signal(text: str) -> bool:
    normalized = text.lower()
    return any(word in normalized for word in COMPLAINT_WORDS)


def has_service_request_signal(text: str) -> bool:
    normalized = text.lower()
    return any(word in normalized for word in SERVICE_REQUEST_WORDS)


def has_policy_query_signal(text: str) -> bool:
    normalized = text.lower()
    return any(word in normalized for word in POLICY_QUERY_WORDS)


def extract_current_input(text: str) -> str:
    marker = "当前输入："
    if marker in text:
        return text.rsplit(marker, 1)[-1].strip()
    return text.strip()


def mock_support_answer(text: str) -> str:
    current = extract_current_input(text)
    if any(word in current for word in ["奖学金", "助学金", "困难补助", "勤工助学"]):
        return (
            "这四类项目的用途不同：\n\n"
            "- **奖学金**：通常侧重学业、综合表现或特定成果。\n"
            "- **助学金**：通常用于支持符合学校资助条件的学生。\n"
            "- **困难补助**：更常对应临时或突发性经济困难。\n"
            "- **勤工助学**：通过校内岗位获得劳动报酬和实践经历。\n\n"
            "可先按“成绩或综合表现、持续经济需要、突发困难、岗位助学”判断要关注哪一类。"
            "具体资格、能否同时申请、金额、名额和截止日期，当前资料未提供，需要核对学校或学院当年通知。"
        )
    if any(word in current for word in ["校园卡", "挂失", "补卡"]):
        return (
            "建议按这个顺序处理：\n\n"
            "1. **先挂失**：立即使用学校官方校园卡平台、自助终端或服务窗口，并保留挂失凭证。\n"
            "2. **核对交易**：查看近期消费；如有异常，保存时间、金额、地点、交易截图和挂失时间，再走学校公布的异常交易申报渠道。\n"
            "3. **再补办**：到官方页面或窗口核对身份核验、材料、费用、领卡和余额转移规则。\n\n"
            "当前资料未提供本校的具体网址、窗口地点、费用和开放时间，请以学校最新通知为准。"
            "请不要在对话中发送完整卡号、支付密码或验证码。"
        )
    if any(word in current for word in ["催办", "投诉", "没处理", "推诿"]):
        return (
            "如果已有原工单且主要是推动进度，**建议先催办，不要重复新建同一报修**。\n\n"
            "催办时整理：原工单编号、首次提交时间、当前状态、已联系渠道、对生活的实际影响，以及希望的回复时间。\n\n"
            "如果已超过学校公开的办理时限、多次催办仍无进展、被重复推诿或对处理结果有争议，"
            "再附上原工单和催办记录，通过学校公布的监督、投诉或申诉渠道升级。"
            "当前资料未提供具体渠道和时限，不建议猜测。"
        )
    if any(word in current for word in ["宿舍", "报修", "空调", "漏水", "维修"]):
        return (
            "首次报修前，建议准备这些信息：\n\n"
            "- 楼栋和房间号\n- 故障设施及具体表现\n- 开始时间、是否持续、影响范围\n"
            "- 可联系时段\n- 不包含无关私人物品的故障照片（如有）\n\n"
            "提交后请保留工单编号和时间，便于后续查询或催办。"
            "当前资料未提供本校具体报修入口和上门时限，请到学校官方后勤平台或最新通知中核对。"
            "如果伴随冒烟、烧焦味或漏电，请先远离设备并联系校园值班人员。"
        )
    return (
        "请先根据校内办事指南核对适用对象、所需材料、官方入口和截止时间。"
        "当前资料没有明确的内容，需要向学校对应部门核实，不建议根据往年经验猜测。"
    )


def split_text(text: str, size: int) -> Iterable[str]:
    for index in range(0, len(text), size):
        yield text[index:index + size]
