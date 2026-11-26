from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import paths


class ProjectPathValidationTests(unittest.TestCase):
    def test_gp_output_does_not_create_catalog_directories_inside_gdb(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root}, clear=True,
        ):
            geodatabase = Path(root) / "data.GDB"
            geodatabase.mkdir()
            output = geodatabase / "FeatureDataset" / "roads"
            self.assertEqual(paths.validate_gp_output_path(str(output), "output"), str(output))
            self.assertFalse((geodatabase / "FeatureDataset").exists())

    def test_gp_output_does_not_create_members_below_database_files(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root}, clear=True,
        ):
            for suffix in (".geodatabase", ".gpkg", ".sqlite", ".mdb"):
                with self.subTest(suffix=suffix):
                    database = Path(root) / ("data" + suffix)
                    database.touch()
                    output = database / "roads"
                    self.assertEqual(paths.validate_gp_output_path(str(output), "output"), str(output))
                    self.assertTrue(database.is_file())

    def test_gp_output_blocks_external_connection_members_but_allows_sde_file_itself(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root}, clear=True,
        ):
            sde_file = Path(root) / "prod.sde"
            self.assertEqual(paths.validate_gp_output_path(str(sde_file), "output"), str(sde_file))
            for blocked in (
                sde_file / "dbo.HackTable",
                sde_file / "FeatureDataset" / "dbo.HackFC",
                Path(root) / "PUB.SDE" / "roads",
                Path(root) / "server.ags" / "MapService",
            ):
                with self.subTest(blocked=str(blocked)):
                    with self.assertRaisesRegex(RuntimeError, r"\.sde/\.ags"):
                        paths.validate_gp_output_path(str(blocked), "output")

    def test_gp_output_still_creates_ordinary_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ, {"ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root}, clear=True,
        ):
            output = Path(root) / "rasters" / "new" / "result.tif"
            paths.validate_gp_output_path(str(output), "output")
            self.assertTrue(output.parent.is_dir())

    def test_write_gate_is_enabled_by_default_and_can_be_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(paths.writes_allowed())
            paths.require_allow_write()

        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"}, clear=True):
            self.assertFalse(paths.writes_allowed())
            with self.assertRaisesRegex(RuntimeError, "ALLOW_WRITE"):
                paths.require_allow_write()

    def test_other_high_impact_gates_remain_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(paths.destructive_allowed())
            self.assertFalse(paths.cim_write_allowed())
            self.assertFalse(paths.publish_allowed())
            self.assertFalse(paths.enterprise_write_allowed())

    def test_path_under_root_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_dir = Path(root) / "Projects"
            project_path = project_dir / "Demo.aprx"
            self.assertTrue(paths.path_under_root(str(project_path).upper(), str(project_dir).lower()))

    def test_validate_project_path_uses_project_roots(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "demo.aprx"
            project_path.touch()
            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_PROJECT_ROOTS": root},
                clear=True,
            ):
                self.assertEqual(
                    paths.validate_project_path(str(project_path)),
                    os.path.normpath(str(project_path)),
                )

    def test_validate_project_path_falls_back_to_input_roots(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            project_path = Path(root) / "demo.aprx"
            project_path.touch()
            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
                clear=True,
            ):
                self.assertEqual(
                    paths.validate_project_path(str(project_path)),
                    os.path.normpath(str(project_path)),
                )

    def test_validate_project_path_requires_absolute_path(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "必须为绝对路径"):
                paths.validate_project_path("demo.aprx")

    def test_validate_project_path_accepts_current_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.validate_project_path("CURRENT"), "CURRENT")
            self.assertEqual(paths.validate_project_path(" current "), "CURRENT")
            self.assertTrue(paths.is_current_project_token('"CURRENT"'))

    def test_output_name_rejects_path_traversal_and_windows_devices(self) -> None:
        self.assertEqual(paths.validate_output_name("roads.shp", "name"), "roads.shp")
        for value in (
            "../escape",
            r"..\escape",
            r"C:\escape",
            "nested/name",
            "name:stream",
            "NUL",
            "LPT1.txt",
            "trailing.",
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                paths.validate_output_name(value, "name")

    def test_new_export_output_rejects_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            output = Path(root, "existing.pdf")
            output.write_bytes(b"keep me")
            with patch.dict(
                os.environ,
                {"ARCGIS_PRO_MCP_EXPORT_ROOT": root},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "拒绝隐式覆盖"):
                    paths.validate_new_output_in_export_root(str(output), "output_path")
            self.assertEqual(output.read_bytes(), b"keep me")

    def test_export_output_requires_configured_root(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {}, clear=True):
            output = Path(root, "result.pdf")
            with self.assertRaisesRegex(RuntimeError, "需要配置绝对路径"):
                paths.validate_output_in_export_root(str(output), "output_path")

    def test_semicolon_multivalue_and_control_chars_are_rejected_everywhere(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
                "ARCGIS_PRO_MCP_EXPORT_ROOT": root,
                "ARCGIS_PRO_MCP_PROJECT_ROOTS": root,
            },
            clear=True,
        ):
            good = Path(root) / "a.shp"
            bad_inputs = [
                rf"{good};C:\Windows\System32\secret.shp",
                f"{good}\x00.shp",
                f"{good}\r\nC:\\evil.shp",
                f"{good}\t.shp",
                f"{good}\x7f.shp",
            ]
            for payload in bad_inputs:
                with self.subTest(payload=payload):
                    self.assertFalse(paths.path_under_root(payload, root))
                    with self.assertRaises(RuntimeError):
                        paths.validate_input_path_optional(payload, "in_features")
                    with self.assertRaises(RuntimeError):
                        paths.validate_gp_output_path(payload, "out_features", create_parent=False)
                    with self.assertRaises(RuntimeError):
                        paths.validate_output_in_export_root(payload, "export_path")
                    with self.assertRaises(RuntimeError):
                        paths.validate_project_path(payload)

            for bad_name in ("a.dbf;b.dbf", "a\x00.dbf", "a\n.dbf", "a\r.dbf", "a\x7f.dbf"):
                with self.subTest(bad_name=bad_name):
                    with self.assertRaises(RuntimeError):
                        paths.validate_output_name(bad_name, "out_name")

            self.assertEqual(paths.validate_project_path("CURRENT:1234"), "CURRENT")
            for bad_current in (r"CURRENT;C:\evil.aprx", "CURRENT\n", "CURRENT:1234;evil", "CURRENT:1234\r"):
                with self.subTest(bad_current=bad_current):
                    with self.assertRaises(RuntimeError):
                        paths.validate_project_path(bad_current)

    def test_ntfs_ads_and_device_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_INPUT_ROOTS": root,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": root,
                "ARCGIS_PRO_MCP_EXPORT_ROOT": root,
                "ARCGIS_PRO_MCP_PROJECT_ROOTS": root,
            },
            clear=True,
        ):
            ads_targets = [
                f"{Path(root) / 'file.tif'}:hidden",
                f"{Path(root) / 'file.tif'}::$DATA",
                f"{Path(root) / 'dir:stream' / 'file.shp'}",
                rf"\\?\{Path(root) / 'file.tif'}",
                rf"\\.\{Path(root) / 'file.tif'}",
            ]
            for target in ads_targets:
                with self.subTest(target=target):
                    self.assertFalse(paths.path_under_root(target, root))
                    with self.assertRaises(RuntimeError):
                        paths.validate_input_path_optional(target, "in_path")
                    with self.assertRaises(RuntimeError):
                        paths.validate_gp_output_path(target, "out_path", create_parent=False)
                    with self.assertRaises(RuntimeError):
                        paths.validate_output_in_export_root(target, "export_path")
                    with self.assertRaises(RuntimeError):
                        paths.validate_project_path(target)

    def test_unc_paths_rejected_without_explicit_roots_and_restricted_with_roots(self) -> None:
        unc_targets = [
            r"\\attacker\share\evil.shp",
            "//attacker/share/evil.shp",
        ]
        with patch.dict(os.environ, {}, clear=True):
            for unc in unc_targets:
                with self.subTest(unc=unc):
                    with self.assertRaisesRegex(RuntimeError, "UNC"):
                        paths.validate_input_path_optional(unc, "in_path")
                    with self.assertRaisesRegex(RuntimeError, "UNC"):
                        paths.validate_gp_output_path(unc, "out_path", create_parent=False)
                    with self.assertRaisesRegex(RuntimeError, "UNC"):
                        paths.validate_project_path(unc)

        with patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": r"\\fileserver\gis_share"},
            clear=True,
        ):
            # Different UNC share must be rejected immediately without network resolution.
            with self.assertRaisesRegex(RuntimeError, "ARCGIS_PRO_MCP_INPUT_ROOTS"):
                paths.validate_input_path_optional(r"\\attacker\share\evil.shp", "in_path")
            with patch("arcgis_pro_mcp.paths.os.path.realpath", side_effect=os.path.normpath):
                allowed = paths.validate_input_path_optional(
                    r"\\fileserver\gis_share\roads.shp", "in_path"
                )
            self.assertEqual(allowed, os.path.normpath(r"\\fileserver\gis_share\roads.shp"))
