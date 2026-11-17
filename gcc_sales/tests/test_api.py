"""HTTP contract and authorization tests for the pilot API."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from gcc_sales.api import app


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.token = "a" * 36
        self.env = patch.dict(os.environ, {
            "NABU_SALES_ADMIN_TOKEN": self.token,
            "NABU_SALES_DB": str(Path(self.tmp.name) / "api.sqlite3"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.header = {"X-Admin-Token": self.token}

    def test_admin_token_required(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertFalse(self.client.get("/health").json()["live_dialing"])
        self.assertEqual(self.client.post("/v1/brands", json={"brand_id": "webinar", "name": "Webinar"}).status_code, 401)

    def test_lead_and_call_blockers(self):
        r = self.client.post("/v1/brands", headers=self.header,
                             json={"brand_id": "webinar", "name": "Webinar"})
        self.assertEqual(r.status_code, 200, r.text)
        lead = self.client.post("/v1/leads", headers=self.header,
                                json={"brand_id": "webinar", "name": "Test Lead",
                                      "phone_e164": "+96891234567"}).json()
        self.assertIn("id", lead)
        r = self.client.get("/v1/leads/" + lead["id"] + "/call-readiness",
                            headers=self.header)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["eligible"])
        self.assertIn("no_documented_marketing_consent", r.json()["reasons"])
        sim = self.client.post("/v1/leads/" + lead["id"] + "/simulate-call",
                               headers=self.header)
        self.assertEqual(sim.json()["status"], "blocked")
        self.assertEqual(self.client.post("/v1/leads/" + lead["id"] + "/opt-out",
                                           headers=self.header).status_code, 200)
        self.assertTrue(self.client.get("/v1/leads/" + lead["id"],
                                         headers=self.header).json()["opted_out"])


if __name__ == "__main__":
    unittest.main()
