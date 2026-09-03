from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import sdk_bridge


class SdkBridgeClientTests(unittest.TestCase):
    def setUp(self) -> None:
        sdk_bridge.clear_local_sessions()
        self.temp = tempfile.TemporaryDirectory()
        self.project = str(Path(self.temp.name) / "project.aprx")
        Path(self.project).touch()
        self.discovery = sdk_bridge._Discovery(
            path=str(Path(self.temp.name) / "bridge-42.json"),
            protocol_version="1",
            process_id=42,
            port=50000,
            token="bearer-secret",
            server_session_id="server-session",
            created_at_utc="2026-09-03T00:00:00Z",
        )
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_PROJECT_ROOTS": self.temp.name,
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
            },
            clear=True,
        )
        self.env.start()

    def tearDown(self) -> None:
        sdk_bridge.clear_local_sessions()
        self.env.stop()
        self.temp.cleanup()

    def _acquire(self) -> str:
        response = {
            "ok": True,
            "lease": {
                "leaseId": "lease-secret",
                "serverSessionId": "server-session",
                "projectUri": self.project,
                "generation": 7,
                "expiresAtUtc": "2026-09-03T00:01:00Z",
            },
        }
        with (
            patch.object(sdk_bridge, "_discoveries", return_value=[self.discovery]),
            patch.object(sdk_bridge, "_request", return_value=response),
        ):
            result = sdk_bridge.acquire_lease(self.project, process_id=42)
        self.assertNotIn("lease-secret", str(result))
        self.assertNotIn("bearer-secret", str(result))
        self.assertEqual(result["lease_generation"], 7)
        return result["sdk_session_ref"]

    def test_acquire_hides_secrets_and_tracks_generation(self) -> None:
        reference = self._acquire()
        self.assertTrue(reference.startswith("arcgis-sdk-session:"))

    def test_edit_undo_sends_concurrency_preconditions(self) -> None:
        reference = self._acquire()
        with patch.object(
            sdk_bridge, "_request", return_value={"ok": True, "edit": {}}
        ) as request:
            sdk_bridge.edit_command(
                reference,
                "undo",
                expected_edit_generation=11,
                expected_map_uri="map://active",
                confirm=True,
            )
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["expectedEditGeneration"], 11)
        self.assertEqual(body["expectedMapUri"], "map://active")

    def test_discard_requires_second_confirmation(self) -> None:
        reference = self._acquire()
        with self.assertRaisesRegex(RuntimeError, "confirm_discard_all"):
            sdk_bridge.edit_command(
                reference,
                "discard",
                expected_edit_generation=11,
                confirm=True,
            )

    def test_job_submit_validates_shape_before_transport(self) -> None:
        reference = self._acquire()
        with patch.object(sdk_bridge, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "tool_name"):
                sdk_bridge.start_gp_job(
                    reference,
                    "management.CopyFeatures;evil",
                    ["in", "out"],
                    confirm=True,
                )
        request.assert_not_called()

    def test_status_never_exposes_discovery_token(self) -> None:
        with (
            patch.object(sdk_bridge, "_discoveries", return_value=[self.discovery]),
            patch.object(
                sdk_bridge,
                "_request",
                return_value={
                    "ok": True,
                    "protocolVersion": "1",
                    "writeEnabled": True,
                    "featureEditsEnabled": True,
                    "gpAllowlistCount": 3,
                    "typedGpContracts": ["management.CopyFeatures"],
                    "capabilities": {"contextSnapshot": True},
                    "context": {"projectUri": self.project},
                },
            ),
        ):
            result = sdk_bridge.bridge_status()
        self.assertNotIn("bearer-secret", str(result))
        self.assertNotIn("server-session", str(result))
        self.assertTrue(result["bridges"][0]["feature_edits_enabled"])
        self.assertEqual(
            result["bridges"][0]["typed_gp_contracts"],
            ["management.CopyFeatures"],
        )

    def test_context_and_camera_use_lease_and_exact_cas_body(self) -> None:
        reference = self._acquire()
        with patch.object(
            sdk_bridge,
            "_request",
            return_value={"ok": True, "context": {"contextGeneration": 9}},
        ) as request:
            sdk_bridge.context_snapshot(reference)
            self.assertEqual(request.call_args.args[2], "/v1/context")
            sdk_bridge.set_camera(
                reference,
                "map://active",
                9,
                3857,
                x=1,
                y=2,
                scale=5000,
                duration_milliseconds=250,
                confirm=True,
            )
        body = request.call_args.kwargs["body"]
        self.assertEqual(
            body,
            {
                "confirm": True,
                "expectedMapUri": "map://active",
                "expectedContextGeneration": 9,
                "expectedSpatialReferenceWkid": 3857,
                "durationMilliseconds": 250,
                "x": 1.0,
                "y": 2.0,
                "scale": 5000.0,
            },
        )

    def test_camera_rejects_invalid_values_before_transport(self) -> None:
        reference = self._acquire()
        with patch.object(sdk_bridge, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "pitch"):
                sdk_bridge.set_camera(
                    reference,
                    "map://active",
                    9,
                    3857,
                    pitch=91,
                    confirm=True,
                )
        request.assert_not_called()

    def test_active_time_requires_explicit_offset_and_pairs(self) -> None:
        reference = self._acquire()
        with self.assertRaisesRegex(RuntimeError, "同时提供"):
            sdk_bridge.set_active_time(
                reference,
                "map://active",
                9,
                start_time="2026-09-03T00:00:00Z",
                confirm=True,
            )
        with self.assertRaisesRegex(RuntimeError, "UTC offset"):
            sdk_bridge.set_active_time(
                reference,
                "map://active",
                9,
                start_time="2026-09-03T00:00:00",
                end_time="2026-09-04T00:00:00",
                confirm=True,
            )

    def test_create_feature_converts_geometry_contract(self) -> None:
        reference = self._acquire()
        geometry = {
            "type": "polygon",
            "spatial_reference_wkid": 3857,
            "coordinates": [[0, 0], [1, 0], [1, 1], [0, 0]],
        }
        with patch.object(
            sdk_bridge,
            "_request",
            return_value={"ok": True, "edit": {"affectedCount": 1}},
        ) as request:
            sdk_bridge.create_feature(
                reference,
                "map://active",
                9,
                "layer://roads",
                4,
                geometry,
                attributes={"Name": "new"},
                confirm=True,
            )
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["geometry"]["spatialReferenceWkid"], 3857)
        self.assertNotIn("spatial_reference_wkid", body["geometry"])
        self.assertEqual(body["expectedEditGeneration"], 4)

    def test_modify_selected_features_sends_selection_cas(self) -> None:
        reference = self._acquire()
        digest = "a" * 64
        with patch.object(
            sdk_bridge,
            "_request",
            return_value={"ok": True, "edit": {"affectedCount": 2}},
        ) as request:
            sdk_bridge.modify_selected_features(
                reference,
                "map://active",
                9,
                "layer://roads",
                4,
                6,
                2,
                digest,
                attributes={"Status": 1},
                confirm=True,
            )
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["expectedSelectionGeneration"], 6)
        self.assertEqual(body["expectedCount"], 2)
        self.assertEqual(body["expectedOidDigest"], digest)

    def test_delete_selected_features_requires_second_confirmation(self) -> None:
        reference = self._acquire()
        with patch.object(sdk_bridge, "_request") as request:
            with self.assertRaisesRegex(RuntimeError, "confirm_delete_selection"):
                sdk_bridge.delete_selected_features(
                    reference,
                    "map://active",
                    9,
                    "layer://roads",
                    4,
                    6,
                    2,
                    "b" * 64,
                    confirm=True,
                )
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
