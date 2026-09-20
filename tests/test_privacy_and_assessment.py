import unittest
import json

from app.core.enums import CampusDepartment, CampusEventCategory, RiskLevel
from app.services.assessment import CampusEventAssessmentService
from app.services.memory import RedisShortTermMemoryStore
from app.services.privacy import PrivacySanitizer


class ExplodingAi:
    def complete(self, messages):
        raise AssertionError("emergency hard guard should not call the model")


class GenericRoutingAi:
    def complete(self, messages):
        return '{"category":"GENERAL","urgency":"ROUTINE","department":"SERVICE_CENTER","risk":"LOW","confidence":0.9,"summary":"普通事务"}'


class PrivacyAndAssessmentTests(unittest.TestCase):
    def test_privacy_sanitizer_masks_common_identifiers(self):
        text = PrivacySanitizer().sanitize("电话 13800138000 邮箱 a@example.com 身份证 110101199003071234")

        self.assertNotIn("13800138000", text)
        self.assertNotIn("a@example.com", text)
        self.assertNotIn("110101199003071234", text)
        self.assertEqual(text.count("[已脱敏]"), 3)

    def test_redis_memory_serializes_sanitized_content(self):
        store = RedisShortTermMemoryStore.__new__(RedisShortTermMemoryStore)
        store.privacy = PrivacySanitizer()

        payload = json.loads(store._serialize("user", "电话 13800138000 邮箱 a@example.com"))

        self.assertNotIn("13800138000", payload["content"])
        self.assertNotIn("a@example.com", payload["content"])
        self.assertEqual(payload["content"].count("[已脱敏]"), 2)

    def test_emergency_signal_uses_hard_guard_before_model(self):
        result = CampusEventAssessmentService(ExplodingAi()).assess("宿舍插座正在冒烟，还有明显烧焦味")

        self.assertEqual(result.risk, RiskLevel.HIGH)
        self.assertGreaterEqual(result.confidence, 0.9)

    def test_domain_routing_overrides_generic_model_route_for_dormitory_repair(self):
        result = CampusEventAssessmentService(GenericRoutingAi()).assess("宿舍空调不制冷，需要报修")

        self.assertEqual(result.category, CampusEventCategory.DORMITORY)
        self.assertEqual(result.department, CampusDepartment.LOGISTICS)


if __name__ == "__main__":
    unittest.main()
