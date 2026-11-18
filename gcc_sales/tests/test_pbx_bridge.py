import hashlib
import hmac
import json
import unittest

import httpx

from gcc_sales.pbx_bridge import make_pbx_event, post_pbx_event, sign_pbx_event


class PbxBridgeTests(unittest.TestCase):
    def test_event_contract_and_signature(self):
        event = make_pbx_event("pbx-call-1", "ended", "101", "+96891234567",
                               extension="101", duration=99)
        body, headers = sign_pbx_event(event, "test-secret-more-than-16", 1760000000)
        digest = hmac.new(
            b"test-secret-more-than-16", b"1760000000." + body, hashlib.sha256
        ).hexdigest()
        self.assertEqual(headers["X-Nabu-Signature"], "sha256=" + digest)
        self.assertEqual(json.loads(body)["call_id"], "pbx-call-1")
        self.assertEqual(json.loads(body)["direction"], "out")

    def test_event_rejects_invalid_recording_and_duration(self):
        with self.assertRaises(ValueError):
            make_pbx_event("a", "started", "101", "+96891234567", recording_url="http://unknown")
        with self.assertRaises(ValueError):
            make_pbx_event("a", "answered", "101", "+96891234567", duration=1)

    def test_sends_to_mock_crm_only(self):
        seen = []
        def sink(request):
            seen.append(request)
            return httpx.Response(200, json={"ok": True})
        response = post_pbx_event(
            make_pbx_event("pbx-call-1", "started", "101", "+96891234567"),
            "https://crm.example.test/api/crm/pbx/test-webhook-key",
            "test-secret-more-than-16", transport=httpx.MockTransport(sink),
        )
        self.assertEqual(response, 200)
        self.assertEqual(len(seen), 1)
        self.assertIn("X-Nabu-Signature", seen[0].headers)
        with self.assertRaises(ValueError):
            post_pbx_event({}, "http://crm.example.test/x", "test-secret-more-than-16")


if __name__ == "__main__":
    unittest.main()
