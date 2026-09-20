from __future__ import annotations

import re


INTERNAL_LINE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:"
    r"应用\s*skill|skill(?:\s*name)?|风险等级|紧急程度|优先级|负责部门|"
    r"置信度|报告\s*id|report\s*id|intent|category|urgency|risk|department|confidence|"
    r"工具计划|队列状态"
    r")\s*[:：=].*$",
    re.IGNORECASE,
)

INTERNAL_TOKEN_REPLACEMENTS = {
    "FACILITY_HAZARD": "设施安全事件",
    "CAMPUS_SECURITY": "校园安全事件",
    "ACADEMIC_AFFAIRS": "教务事务",
    "STUDENT_AFFAIRS": "学生事务",
    "SERVICE_CENTER": "一站式服务中心",
    "CAMPUS_CARD": "校园卡服务",
    "FINANCIAL_AID": "学生资助",
    "LOGISTICS": "后勤部门",
    "SECURITY": "校园保卫部门",
    "IT_CENTER": "信息服务部门",
    "PRIORITY": "需尽快处理",
    "URGENT": "需立即处理",
    "ROUTINE": "常规处理",
    "HIGH": "需立即处理",
    "MEDIUM": "需尽快处理",
    "LOW": "一般情况",
}

FALSE_SUCCESS_PATTERNS = (
    re.compile(
        r"(?:我|系统|后台)(?:已经|已)(?:成功)?(?:为你)?"
        r"(?:创建|提交|受理|派发|发送|通知|升级|挂失|补办|建单)[^。！？\n]*[。！？]?"
    ),
    re.compile(
        r"(?:工单|申请|通知|投诉|催办)(?:已经|已)(?:成功)?"
        r"(?:创建|提交|受理|派发|发送|通知|升级)[^。！？\n]*[。！？]?"
    ),
    re.compile(r"维修人员(?:已经|已)(?:接单|到场)[^。！？\n]*[。！？]?"),
)


def sanitize_student_response(text: str) -> str:
    """Remove internal metadata and pre-dispatch success claims from student-visible text."""
    cleaned_lines = [line for line in str(text).splitlines() if not INTERNAL_LINE.match(line)]
    cleaned = "\n".join(cleaned_lines)

    for token, replacement in INTERNAL_TOKEN_REPLACEMENTS.items():
        cleaned = re.sub(rf"(?<![A-Z_]){re.escape(token)}(?![A-Z_])", replacement, cleaned, flags=re.IGNORECASE)

    cleaned = re.sub(
        r"[A-Za-z0-9][A-Za-z0-9_.-]*\.(?:md|pdf|json|ya?ml)",
        "校内办事指南",
        cleaned,
        flags=re.IGNORECASE,
    )
    for pattern in FALSE_SUCCESS_PATTERNS:
        cleaned = pattern.sub("当前回复不能确认后台办理已成功。", cleaned)

    cleaned = re.sub(r"(?:\n[ \t]*){3,}", "\n\n", cleaned)
    return cleaned.strip()


def finalize_student_response(text: str, user_input: str) -> str:
    """Apply deterministic student-facing boundaries that small local models may omit."""
    cleaned = sanitize_student_response(text)
    if _is_actionable_dormitory_repair(user_input):
        cleaned = _remove_duplicate_submission_instructions(cleaned)
        if "CampusFlow" not in cleaned or "自动登记" not in cleaned:
            cleaned = _append_boundary(
                cleaned,
                "你提供的楼栋、房间、故障情况和可联系时段已可用于登记报修。"
                "CampusFlow 会在本轮结束后自动创建内部服务工单，无需前往其他平台重复提交。"
                "只有在后台返回成功结果后，下方才会显示真实工单编号。",
            )
    financial_aid_terms = ["奖学金", "助学金", "困难补助", "勤工助学"]
    if ("区别" in user_input or "怎么选" in user_input) and sum(term in user_input for term in financial_aid_terms) >= 2:
        cleaned = _financial_aid_difference_answer()
    elif any(term in user_input for term in financial_aid_terms):
        if not any(term in cleaned for term in ["当年通知", "当年的通知", "当前资料未提供"]):
            cleaned = _append_boundary(
                cleaned,
                "以上是项目的一般性区分，不代表本校的具体资格认定。"
                "具体条件、能否同时申请、金额、名额和截止日期，请核对学校或学院当年通知。",
            )
    if any(term in user_input for term in ["校园卡", "饭卡"]) and "挂失" in user_input and "补办" in user_input:
        if "先挂失" not in cleaned or "补办" not in cleaned:
            cleaned = _append_boundary(
                cleaned,
                "完整处理顺序是：先挂失，再核对近期交易，最后补办。"
                "补办前请到学校官方页面或窗口核对身份核验、所需材料、费用、领卡方式和余额转移规则；"
                "当前资料未提供本校的具体地点、费用和开放时间。",
            )
        if not any(term in cleaned for term in ["验证码", "支付密码"]):
            cleaned = _append_boundary(
                cleaned,
                "不要在对话中发送完整校园卡号、支付密码或验证码；身份核验请在学校官方页面或线下窗口完成。",
            )
    return cleaned


def _append_boundary(text: str, boundary: str) -> str:
    return f"{text.rstrip()}\n\n{boundary}" if text.strip() else boundary


def _is_actionable_dormitory_repair(user_input: str) -> bool:
    has_repair = any(term in user_input for term in ["报修", "空调", "水管", "门锁", "灯具", "漏水", "维修"])
    has_building = any(term in user_input for term in ["楼栋", "楼号", "宿舍楼"])
    has_room = any(term in user_input for term in ["房间", "房号", "寝室"])
    return has_repair and has_building and has_room


def _remove_duplicate_submission_instructions(text: str) -> str:
    kept = []
    for line in text.splitlines():
        normalized = line.replace("**", "")
        if "登录校园服务平台" in normalized:
            continue
        if "提交报修信息" in normalized and any(term in normalized for term in ["填写", "平台", "入口"]):
            continue
        kept.append(line.replace("提交报修信息后", "工单创建后"))
    return "\n".join(kept).strip()


def _financial_aid_difference_answer() -> str:
    return (
        "这四类项目的用途不同：\n\n"
        "- **奖学金**：主要表彰学业、综合表现或特定成果。\n"
        "- **助学金**：主要支持符合学校资助条件的学生。\n"
        "- **困难补助**：常面向临时或突发性经济困难。\n"
        "- **勤工助学**：学生通过校内岗位获得劳动报酬和实践经历。\n\n"
        "可先按自己的主要需求选择要查看的项目：学业或综合表现对应奖学金，"
        "持续性经济需求对应助学金，突发困难对应困难补助，岗位助学需求对应勤工助学。\n\n"
        "以上是一般性区分，不代表本校的具体资格认定。"
        "当前资料未提供成绩门槛、家庭经济认定方式、能否同时申请、金额、名额和截止日期，"
        "请核对学校或学院当年通知。"
    )


def student_response_chunks(text: str, size: int = 12):
    for index in range(0, len(text), size):
        yield text[index:index + size]
