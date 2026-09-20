import unittest

from app.services.student_response import finalize_student_response, sanitize_student_response


class StudentResponseTests(unittest.TestCase):
    def test_removes_internal_lines_and_codes(self):
        text = (
            "应用 skill: dormitory_repair\n"
            "风险等级：HIGH\n"
            "负责部门：LOGISTICS\n"
            "请先远离设备，该事项为 PRIORITY。"
        )

        cleaned = sanitize_student_response(text)

        for forbidden in ["skill", "HIGH", "LOGISTICS", "PRIORITY", "风险等级"]:
            self.assertNotIn(forbidden, cleaned)
        self.assertIn("请先远离设备", cleaned)

    def test_replaces_pre_dispatch_success_claims(self):
        text = "系统已经为你创建工单 CF-1001。维修人员已接单。"

        cleaned = sanitize_student_response(text)

        self.assertNotIn("已经为你创建", cleaned)
        self.assertNotIn("已接单", cleaned)
        self.assertIn("不能确认后台办理已成功", cleaned)

    def test_hides_internal_source_filename(self):
        cleaned = sanitize_student_response("根据 dormitory-repair-guide.md，请保留原工单。")

        self.assertNotIn(".md", cleaned)
        self.assertIn("校内办事指南", cleaned)

    def test_adds_missing_financial_aid_policy_boundary(self):
        cleaned = finalize_student_response(
            "奖学金看学业表现，助学金关注经济需求。",
            "奖学金和助学金有什么区别？",
        )

        self.assertIn("不代表本校的具体资格认定", cleaned)
        self.assertIn("当年通知", cleaned)

    def test_financial_aid_difference_uses_grounded_canonical_answer(self):
        cleaned = finalize_student_response(
            "奖学金申请资格包括科研成果和社会活动参与度。",
            "奖学金、助学金、困难补助和勤工助学有什么区别？我该怎么选？",
        )

        self.assertNotIn("科研成果", cleaned)
        self.assertNotIn("社会活动参与度", cleaned)
        self.assertIn("当前资料未提供成绩门槛", cleaned)

    def test_adds_missing_card_replacement_step(self):
        cleaned = finalize_student_response(
            "请立即通过学校官方平台挂失，并保留挂失凭证。",
            "校园卡丢了，怎么先挂失再补办？",
        )

        self.assertIn("先挂失", cleaned)
        self.assertIn("最后补办", cleaned)
        self.assertIn("当前资料未提供", cleaned)

    def test_actionable_repair_removes_duplicate_submission_and_explains_auto_ticket(self):
        cleaned = finalize_student_response(
            "1. 登录校园服务平台。\n2. 在平台填写并提交报修信息。\n提交报修信息后请保留编号。",
            "楼栋号 B25，房间号 606，宿舍空调不制冷，可联系时段为后天下午，请报修。",
        )

        self.assertNotIn("登录校园服务平台", cleaned)
        self.assertNotIn("平台填写并提交", cleaned)
        self.assertIn("自动创建内部服务工单", cleaned)
        self.assertIn("无需前往其他平台重复提交", cleaned)


if __name__ == "__main__":
    unittest.main()
