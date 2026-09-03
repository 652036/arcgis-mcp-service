from __future__ import annotations

import unittest

from arcgis_pro_mcp.redaction import redact_sensitive, redact_text, safe_error


class RedactionTests(unittest.TestCase):
    def test_redacts_connection_strings_headers_and_urls(self) -> None:
        raw = (
            "password=hunter2; api_key='abc'; Authorization: Bearer ey.secret.token "
            "https://alice:pw@example.test/path?token=qwerty&mode=read"
        )
        result = redact_text(raw)
        for secret in ("hunter2", "abc", "ey.secret.token", "alice", "pw", "qwerty"):
            self.assertNotIn(secret, result)
        self.assertIn("mode=read", result)
        self.assertIn("[REDACTED]", result)

    def test_recursive_redaction_keeps_nonsecret_values(self) -> None:
        result = redact_sensitive(
            {
                "connection": "server=db;password=hidden;dataset=roads",
                "access_token": "opaque",
                "nested": [{"name": "Roads", "url": "https://host/?api-key=hidden2"}],
            }
        )
        self.assertEqual(result["access_token"], "[REDACTED]")
        self.assertEqual(result["nested"][0]["name"], "Roads")
        self.assertNotIn("hidden", repr(result))
        self.assertNotIn("hidden2", repr(result))

    def test_safe_error_is_bounded(self) -> None:
        self.assertEqual(safe_error("token=secret " + "x" * 50, 20), "token=[REDACTED] xxx")


if __name__ == "__main__":
    unittest.main()
