import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from gcc_sales.core import SalesStore
from gcc_sales.crm_sync import APP_ID, CrmDispatcher, make_payload, signed_request


class CrmSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SalesStore(Path(self.tmp.name) / "store.sqlite3")
        self.store.create_brand("webinar_oman", "Webinar Oman")
        self.lead = self.store.upsert_lead("webinar_oman", "Sara", "+96891234567")

    def test_signature_matches_crm_contract(self):
        payload = make_payload(self.lead)
        raw, headers = signed_request(payload, "supersecretfortestonly", 1760000000)
        expected = hmac.new(b"supersecretfortestonly",
                            (APP_ID + ":1760000000:").encode() + raw, hashlib.sha256).hexdigest()
        self.assertEqual(headers["X-NabuGate-Signature"], expected)
        self.assertEqual(headers["X-NabuGate-App-Id"], "sales-agent")
        self.assertEqual(json.loads(raw)["consent"]["sms"], False)
        self.assertEqual(payload["person"]["country"], "OM")

    def test_success_is_recorded_and_not_resent(self):
        sent = []
        def receive(req):
            sent.append(req)
            return httpx.Response(201, json={"ok": True, "duplicate": False})
        dispatcher = CrmDispatcher(self.store, "supersecretfortestonly",
                                   transport=httpx.MockTransport(receive))
        self.assertEqual(len(dispatcher.pending()), 1)
        self.assertTrue(dispatcher.run_once()[0]["delivered"])
        self.assertEqual(len(sent), 1)
        self.assertEqual(dispatcher.pending(), [])

    def test_bad_signature_is_permanent_not_retried(self):
        dispatcher = CrmDispatcher(self.store, "supersecretfortestonly",
                                   transport=httpx.MockTransport(lambda req: httpx.Response(403)))
        result = dispatcher.run_once()[0]
        self.assertFalse(result["delivered"])
        self.assertTrue(result["permanent_failure"])
        self.assertEqual(dispatcher.pending(), [])

    def test_network_error_retries_later(self):
        def receive(request):
            raise httpx.ConnectError("offline", request=request)
        dispatcher = CrmDispatcher(self.store, "supersecretfortestonly",
                                   transport=httpx.MockTransport(receive))
        result = dispatcher.run_once()[0]
        self.assertFalse(result["delivered"])
        self.assertFalse(result["permanent_failure"])
        self.assertEqual(dispatcher.pending(), [])

    def test_refuses_http_or_missing_secret(self):
        with self.assertRaises(ValueError):
            CrmDispatcher(self.store, "")
        with self.assertRaises(ValueError):
            CrmDispatcher(self.store, "supersecretfortestonly", url="http://localhost:80")


if __name__ == "__main__":
    unittest.main()
