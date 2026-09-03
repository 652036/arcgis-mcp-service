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

