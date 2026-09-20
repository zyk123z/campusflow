import unittest
from datetime import datetime, timedelta

from app.services.sla import sla_deadline, sla_minutes_for, sla_status


class ServiceLevelAgreementTests(unittest.TestCase):
    def test_urgency_maps_to_expected_minutes(self):
        self.assertEqual(sla_minutes_for("URGENT"), 5)
        self.assertEqual(sla_minutes_for("PRIORITY"), 240)
        self.assertEqual(sla_minutes_for("ROUTINE"), 1440)

    def test_unknown_urgency_falls_back_to_routine(self):
        self.assertEqual(sla_minutes_for("UNKNOWN"), 1440)

    def test_sla_status_respects_deadline_and_completion(self):
        created_at = datetime(2026, 1, 1, 8, 0, 0)
        deadline = sla_deadline(created_at, "URGENT")
        self.assertEqual(deadline, created_at + timedelta(minutes=5))
        self.assertEqual(sla_status(deadline, "OPEN", created_at + timedelta(minutes=4)), "ACTIVE")
        self.assertEqual(sla_status(deadline, "OPEN", created_at + timedelta(minutes=6)), "OVERDUE")
        self.assertEqual(sla_status(deadline, "ACKNOWLEDGED", created_at + timedelta(minutes=6)), "COMPLETED")


if __name__ == "__main__":
    unittest.main()
