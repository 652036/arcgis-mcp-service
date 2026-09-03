from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import publishing


class FakeResult:
    def __init__(self, messages=None) -> None:
        self.messages = list(messages or [])
        self.messageCount = len(self.messages)

    def getMessage(self, index):
        return self.messages[index]


class FakeSharingSettings:
    def __init__(self) -> None:
        self.sharingLevel = "OWNER"
        self.groups = ""


class FakeDraft:
    def __init__(self) -> None:
        self.summary = ""
        self.tags = ""
        self.description = ""
        self.credits = ""
        self.useLimitations = ""
        self.portalFolder = ""
        self.serverFolder = ""
        self.federatedServerUrl = ""
        self.targetServer = ""
        self.overwriteExistingService = False
        self.copyDataToServer = True
        self.sharing = FakeSharingSettings()
        self.exports = []

    def exportToSDDraft(self, path):
        self.exports.append(path)
        Path(path).write_bytes(b"safe service definition draft")

    def analyzeSDDraft(self):
        return {"errors": [], "warnings": [(100, "token=hidden")], "token": "never return"}


class FakeSource:
    def __init__(self) -> None:
        self.calls = []
        self.draft = FakeDraft()

    def getWebLayerSharingDraft(self, server_type, service_type, service_name):
        self.calls.append((server_type, service_type, service_name))
        return self.draft


class FakeSharingModule:
    def __init__(self) -> None:
        self.calls = []
        self.last_draft = None

    def CreateSharingDraft(self, server_type, service_type, service_name, source):
        self.calls.append((server_type, service_type, service_name, source))
        self.last_draft = FakeDraft()
        return self.last_draft


class FakeServerModule:
    def __init__(self) -> None:
        self.stage_calls = []
        self.upload_calls = []

    def StageService(self, source, output, *args):
        self.stage_calls.append((source, output, args))
        Path(output).write_bytes(b"staged service definition")
        return FakeResult(["Analyzer completed", "token=do-not-return"])

    def UploadServiceDefinition(self, source, target):
        self.upload_calls.append((source, target))
        return FakeResult(["Published", "password=do-not-return"])


class FakeArcpy:
    def __init__(self, portal_url="https://portal.example.com/portal") -> None:
        self.portal_url = portal_url
        self.sharing = FakeSharingModule()
        self.server = FakeServerModule()

    def GetActivePortalURL(self):
        return self.portal_url

    @staticmethod
    def GetSigninToken():
        return {"token": "super-secret", "expires": 123}

    @staticmethod
    def GetPortalInfo(**kwargs):
        del kwargs
        return {
            "SSL_enabled": True,
            "organization": "Example Org",
            "portal_version": "11.4",
            "role": "account_publisher",
            "token": "must-not-return",
            "password": "must-not-return",
        }


class PublishingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.portal_url = "https://portal.example.com/portal"
        self.server_url = "https://server.example.com/server"
        self.ags_path = Path(self.temp_dir.name, "publisher.ags")
        self.ags_path.write_bytes(b"encrypted connection")
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_PUBLISH": "1",
                "ARCGIS_PRO_MCP_ALLOW_PUBLIC_SHARE": "0",
                "ARCGIS_PRO_MCP_ALLOW_PUBLISH_OVERWRITE": "0",
                "ARCGIS_PRO_MCP_EXPORT_ROOT": self.temp_dir.name,
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp_dir.name,
                "ARCGIS_PRO_MCP_PORTAL_ALLOWLIST": self.portal_url,
                "ARCGIS_PRO_MCP_SERVER_ALLOWLIST": f"{self.server_url};{self.ags_path}",
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_portal_status_never_returns_token_or_password(self):
        status = publishing.portal_status(FakeArcpy())
        self.assertTrue(status["signed_in"])
        self.assertTrue(status["allowlisted"])
        self.assertEqual(status["portal_info"]["organization"], "Example Org")
        rendered = repr(status).lower()
        self.assertNotIn("super-secret", rendered)
        self.assertNotIn("must-not-return", rendered)

    def test_create_hosted_draft_sets_typed_properties_and_returns_digest(self):
        arcpy = FakeArcpy()
        source = FakeSource()
        output = os.path.join(self.temp_dir.name, "drafts", "Parcels.sddraft")
        result = publishing.create_sharing_draft(
            arcpy,
            source,
            "Parcels",
            output,
            portal_url=self.portal_url,
            sharing_level="ORGANIZATION",
            groups=["Editors"],
            tags=["land", "reviewed"],
            summary="Parcel service",
            portal_folder="Production",
            copy_data_to_server=True,
        )
        self.assertEqual(source.calls, [("HOSTING_SERVER", "FEATURE", "Parcels")])
        self.assertEqual(source.draft.sharing.sharingLevel, "ORGANIZATION")
        self.assertEqual(source.draft.sharing.groups, "Editors")
        self.assertEqual(source.draft.tags, "land,reviewed")
        self.assertEqual(len(result["artifact"]["sha256"]), 64)
        self.assertTrue(result["analysis"]["available"])
        self.assertNotIn("never return", repr(result))

    def test_scene_layer_uses_arcgis_pro_service_type_enum(self):
        arcpy = FakeArcpy()
        source = FakeSource()
        output = os.path.join(self.temp_dir.name, "drafts", "Buildings.sddraft")
        publishing.create_sharing_draft(
            arcpy,
            source,
            "Buildings",
            output,
            portal_url=self.portal_url,
            service_type="SCENE_LAYER",
        )
        self.assertEqual(
            source.calls,
            [("HOSTING_SERVER", "SCENE_LAYER", "Buildings")],
        )

    def test_draft_and_staged_service_refuse_existing_outputs(self):
        draft_output = Path(self.temp_dir.name, "existing.sddraft")
        draft_output.write_bytes(b"original draft")
        source = FakeSource()
        with self.assertRaisesRegex(RuntimeError, "拒绝隐式覆盖"):
            publishing.create_sharing_draft(
                FakeArcpy(),
                source,
                "Existing",
                str(draft_output),
                portal_url=self.portal_url,
            )
        self.assertEqual(draft_output.read_bytes(), b"original draft")
        self.assertEqual(source.draft.exports, [])

        input_draft = Path(self.temp_dir.name, "input-existing-test.sddraft")
        input_draft.write_bytes(b"input")
        staged_output = Path(self.temp_dir.name, "existing.sd")
        staged_output.write_bytes(b"original service")
        digest = publishing.artifact_digest(str(input_draft))["sha256"]
        arcpy = FakeArcpy()
        with self.assertRaisesRegex(RuntimeError, "拒绝隐式覆盖"):
            publishing.stage_service_definition(
                arcpy,
                str(input_draft),
                str(staged_output),
                expected_sha256=digest,
            )
        self.assertEqual(staged_output.read_bytes(), b"original service")
        self.assertEqual(arcpy.server.stage_calls, [])

    def test_public_and_overwrite_have_independent_gates(self):
        source = FakeSource()
        output = os.path.join(self.temp_dir.name, "public.sddraft")
        with self.assertRaisesRegex(RuntimeError, "公开共享已禁用"):
            publishing.create_sharing_draft(
                FakeArcpy(),
                source,
                "PublicLayer",
                output,
                sharing_level="EVERYONE",
            )
        with self.assertRaisesRegex(RuntimeError, "覆盖发布已禁用"):
            publishing.create_sharing_draft(
                FakeArcpy(),
                source,
                "OverwriteLayer",
                output,
                overwrite_existing_service=True,
            )

    def test_publish_gate_and_target_allowlist_are_mandatory(self):
        source = FakeSource()
        output = os.path.join(self.temp_dir.name, "blocked.sddraft")
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_PUBLISH": "0"}):
            with self.assertRaisesRegex(RuntimeError, "发布操作已禁用"):
                publishing.create_sharing_draft(FakeArcpy(), source, "Blocked", output)
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_PORTAL_ALLOWLIST": "https://other.example.com"}):
            with self.assertRaisesRegex(RuntimeError, "白名单"):
                publishing.create_sharing_draft(FakeArcpy(), source, "WrongPortal", output)

    def test_inline_credentials_are_rejected_before_export(self):
        output = os.path.join(self.temp_dir.name, "secret.sddraft")
        with self.assertRaisesRegex(RuntimeError, "禁止包含"):
            publishing.create_sharing_draft(
                FakeArcpy(),
                FakeSource(),
                "NoSecrets",
                output,
                description="password=hunter2",
            )
        with self.assertRaisesRegex(RuntimeError, "用户名或密码"):
            publishing.create_sharing_draft(
                FakeArcpy(),
                FakeSource(),
                "NoUrlSecrets",
                output,
                portal_url="https://alice:secret@portal.example.com/portal",
            )

    def test_federated_map_image_binds_the_exact_allowlisted_server(self):
        source = FakeSource()
        output = os.path.join(self.temp_dir.name, "map_image.sddraft")
        publishing.create_sharing_draft(
            FakeArcpy(),
            source,
            "Map_Image",
            output,
            server_type="FEDERATED_SERVER",
            service_type="MAP_IMAGE",
            portal_url=self.portal_url,
            federated_server_url=self.server_url,
        )
        self.assertEqual(source.draft.federatedServerUrl, self.server_url)

    def test_stage_requires_matching_digest_and_returns_output_digest(self):
        arcpy = FakeArcpy()
        source = Path(self.temp_dir.name, "input.sddraft")
        source.write_bytes(b"draft")
        identity = publishing.artifact_digest(str(source))
        output = os.path.join(self.temp_dir.name, "output.sd")
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            publishing.stage_service_definition(
                arcpy,
                str(source),
                output,
                expected_sha256="0" * 64,
            )
        result = publishing.stage_service_definition(
            arcpy,
            str(source),
            output,
            expected_sha256=identity["sha256"],
            staging_version=350,
        )
        self.assertEqual(len(result["artifact"]["sha256"]), 64)
        self.assertEqual(arcpy.server.stage_calls[0][2], (350,))
        self.assertNotIn("do-not-return", repr(result))

    def test_publish_upload_is_attested_and_uses_allowlisted_target(self):
        arcpy = FakeArcpy()
        source = Path(self.temp_dir.name, "service.sd")
        source.write_bytes(b"service")
        digest = publishing.artifact_digest(str(source))["sha256"]
        result = publishing.publish_service_definition(
            arcpy,
            str(source),
            expected_sha256=digest,
            portal_url=self.portal_url,
        )
        self.assertTrue(result["published"])
        self.assertEqual(arcpy.server.upload_calls, [(str(source), "HOSTING_SERVER")])
        self.assertNotIn("do-not-return", repr(result))

    def test_standalone_create_uses_allowlisted_ags_without_returning_its_path(self):
        arcpy = FakeArcpy()
        output = os.path.join(self.temp_dir.name, "map_service.sddraft")
        result = publishing.create_sharing_draft(
            arcpy,
            object(),
            "Map_Service",
            output,
            server_type="STANDALONE_SERVER",
            service_type="MAP_SERVICE",
            server_connection=str(self.ags_path),
        )
        self.assertEqual(arcpy.sharing.calls[0][:3], ("STANDALONE_SERVER", "MAP_SERVICE", "Map_Service"))
        self.assertEqual(arcpy.sharing.last_draft.targetServer, str(self.ags_path))
        self.assertNotIn(str(self.ags_path), repr(result))

    def test_artifact_digest_rejects_files_outside_export_root(self):
        with tempfile.TemporaryDirectory() as other_dir:
            outside = Path(other_dir, "outside.sd")
            outside.write_bytes(b"not a publishing artifact in the controlled root")
            with self.assertRaisesRegex(RuntimeError, "EXPORT_ROOT"):
                publishing.artifact_digest(str(outside))


if __name__ == "__main__":
    unittest.main()
