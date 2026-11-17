"""Offline tests: python -m unittest discover -s gcc_sales/tests -v"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from gcc_sales.core import SalesStore


class GCCSalesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = SalesStore(Path(self.tmp.name) / "sales.sqlite3")
        self.db.create_brand("webinar_oman", "Webinar Oman")
        self.lead = self.db.upsert_lead(
            "webinar_oman", "Test Prospect", "+96891234567",
            consent_reference="signed-form:internal-test-001",
            consent_at="2026-10-01T10:00:00+04:00",
        )

    def test_country_is_inferred(self):
        self.assertEqual(self.lead["country"], "OM")

    def test_brand_unapproved_blocks_call(self):
        r = self.db.call_eligibility(self.lead["id"], at=self.local(12))
        self.assertFalse(r["eligible"])
        self.assertIn("brand_outbound_disabled", r["reasons"])

    def test_oman_hours_and_simulation(self):
        self.db.authorize_brand("webinar_oman", True, ["OM"], "TRA/operator-test-approval")
        self.assertFalse(self.db.call_eligibility(self.lead["id"], self.local(7))["eligible"])
        self.assertTrue(self.db.call_eligibility(self.lead["id"], self.local(12))["eligible"])
        r = self.db.simulate_call(self.lead["id"], self.local(12))
        self.assertEqual(r["status"], "simulated")
        self.assertFalse(r["network_called"])
        self.assertEqual(self.db.metrics("webinar_oman")["simulated_calls"], 1)

    def test_leads_without_consent_blocked(self):
        lead = self.db.upsert_lead("webinar_oman", "No consent", "+96892345678")
        self.db.authorize_brand("webinar_oman", True, ["OM"], "TRA/operator-test-approval")
        r = self.db.call_eligibility(lead["id"], self.local(12))
        self.assertIn("no_documented_marketing_consent", r["reasons"])

    def test_opt_out_not_overridden_by_reimport(self):
        self.db.authorize_brand("webinar_oman", True, ["OM"], "TRA/operator-test-approval")
        self.db.opt_out(self.lead["id"])
        self.db.upsert_lead("webinar_oman", "Reimport", "+96891234567",
                            consent_reference="signed-form:reimport",
                            consent_at="2026-10-05T10:00:00+04:00")
        r = self.db.call_eligibility(self.lead["id"], self.local(12))
        self.assertIn("do_not_contact", r["reasons"])

    def test_same_phone_in_different_brands(self):
        self.db.create_brand("udrive_oman", "Udrive")
        other = self.db.upsert_lead("udrive_oman", "Different", "+96891234567")
        self.assertNotEqual(self.lead["id"], other["id"])
        self.assertEqual(self.db.metrics("udrive_oman")["leads"], 1)

    def test_meeting_prevents_overlap(self):
        start = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
        end = start + timedelta(minutes=30)
        result = self.db.book_meeting(self.lead["id"], "rep_1", start.isoformat(), end.isoformat())
        self.assertEqual(result["status"], "confirmed")
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.db.book_meeting(self.lead["id"], "rep_1",
                                 (start + timedelta(minutes=15)).isoformat(),
                                 (end + timedelta(minutes=15)).isoformat())
        self.assertEqual(self.db.metrics("webinar_oman")["meetings_confirmed"], 1)

    def test_invalid_number_and_timezone(self):
        with self.assertRaises(ValueError):
            self.db.upsert_lead("webinar_oman", "Invalid", "91234567")
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.db.book_meeting(self.lead["id"], "rep_1",
                                 "2030-02-01T10:00:00", "2030-02-01T11:00:00")

    @staticmethod
    def local(hour):
        return datetime(2026, 11, 17, hour, 0, tzinfo=ZoneInfo("Asia/Muscat"))


if __name__ == "__main__":
    unittest.main()
