from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import paths


class ProjectPathValidationTests(unittest.TestCase):
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

