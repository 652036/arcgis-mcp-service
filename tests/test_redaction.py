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

    def test_redacts_prefixed_and_quoted_credential_keys(self) -> None:
        raw = (
            "ENCRYPTED_PASSWORD=sde_secret_1; "
            "ENCRYPTED_PASSWORD_UTF8=sde_secret_utf8; "
            "DB_PASSWORD=db_secret_2; "
            "PORTAL_TOKEN=portal_tok_3; "
            "AUTH_TOKEN=auth_tok_4; "
            "X-ArcGIS-Pro-MCP-Token: header_tok_5; "
            "db_pwd=short_pwd_6; "
            "signing_private_key='priv_7'; "
            'service_api_key="api_8"; '
            "portal_credential=cred_9; "
            '{"DB_PASSWORD": "json_secret_10"}; '
            "https://host.test/api?portal_token=qtok_11&db_password=qpw_12&mode=read"
        )
        result = redact_text(raw)
        for secret in (
            "sde_secret_1",
            "sde_secret_utf8",
            "db_secret_2",
            "portal_tok_3",
            "auth_tok_4",
            "header_tok_5",
            "short_pwd_6",
            "priv_7",
            "api_8",
            "cred_9",
            "json_secret_10",
            "qtok_11",
            "qpw_12",
        ):
            self.assertNotIn(secret, result)
        self.assertIn("mode=read", result)

    def test_preserves_non_secret_assignments_and_normal_text(self) -> None:
        raw = (
            r"dataset=roads; mode=read; max_tokens=100; token_type=bearer; "
            r"password_policy=strict; design=1; assign=2; cwd=C:\work; "
            r'output_path=C:\out\a.shp; Error "invalid password": check config'
        )
        self.assertEqual(redact_text(raw), raw)


if __name__ == "__main__":
    unittest.main()
