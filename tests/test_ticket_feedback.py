import unittest

from app.agents.harness import AgentToolDispatchResult


class TicketFeedbackTests(unittest.TestCase):
    def test_confirmed_ticket_uses_student_facing_status_and_department(self):
        result = AgentToolDispatchResult(
            state="CONFIRMED",
            ticket_id=23,
            ticket_status="OPEN",
            department="LOGISTICS",
        )

        message = result.student_message()

        self.assertIn("工单编号：23", message)
        self.assertIn("当前状态：待处理", message)
        self.assertIn("负责部门：后勤部门", message)
        self.assertNotIn("OPEN", message)
        self.assertNotIn("LOGISTICS", message)

    def test_queued_ticket_does_not_claim_success(self):
        message = AgentToolDispatchResult(state="QUEUED").student_message()

        self.assertIn("正在后台创建", message)
        self.assertNotIn("工单编号：", message)


if __name__ == "__main__":
    unittest.main()
