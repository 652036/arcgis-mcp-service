from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import project_catalog


class _Project:
    def __init__(self, root: str) -> None:
        self.folderConnections = [
            {"connectionString": root, "alias": "Home", "isHomeFolder": True}
        ]
        self.databases = [
            {"databasePath": str(Path(root) / "default.gdb"), "isDefaultDatabase": True}
        ]
        self.toolboxes = [
            {"toolboxPath": str(Path(root) / "default.atbx"), "isDefaultToolbox": True}
        ]
        self.styles = ["ArcGIS 2D"]

    def updateFolderConnections(self, values, validate):
        self.folderConnections = values
        return []

    def updateDatabases(self, values, validate):
        self.databases = values
        return []

    def updateToolboxes(self, values, validate):
        self.toolboxes = values
        return []

    def updateStyles(self, values):
        self.styles = values
        return []

    def listStyleItems(self, style, style_class, wildcard):
        return [
            SimpleNamespace(
                name="Airport",
                category="Transportation",
                tags="airport",
                styleClass=style_class,
            )
        ]


class ProjectCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.project = _Project(self.root)
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.root,
            },
            clear=True,
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp.cleanup()

    def test_add_and_remove_non_default_catalog_items(self) -> None:
        folder = str(Path(self.root) / "data")
        database = str(Path(self.root) / "data.gdb")
        toolbox = str(Path(self.root) / "tools.atbx")
        for value in (folder, database, toolbox):
            Path(value).mkdir() if "." not in Path(value).name else Path(value).touch()

        self.assertTrue(
            project_catalog.add_folder_connection(self.project, folder)["verified"]
        )
        self.assertTrue(project_catalog.add_database(self.project, database)["verified"])
        self.assertTrue(project_catalog.add_toolbox(self.project, toolbox)["verified"])
        self.assertTrue(
            project_catalog.remove_folder_connection(self.project, folder)["removed"]
        )
        self.assertTrue(project_catalog.remove_database(self.project, database)["removed"])
        self.assertTrue(project_catalog.remove_toolbox(self.project, toolbox)["removed"])

    def test_python_toolbox_is_never_loaded_through_project_catalog(self) -> None:
        toolbox = str(Path(self.root) / "unsafe.pyt")
        Path(toolbox).write_text("raise RuntimeError('must not execute')", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "不加载可执行 Python .pyt"):
            project_catalog.add_toolbox(self.project, toolbox)

    def test_refuses_to_remove_home_and_defaults(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "home folder"):
            project_catalog.remove_folder_connection(self.project, self.root)
        with self.assertRaisesRegex(RuntimeError, "default geodatabase"):
            project_catalog.remove_database(
                self.project, str(Path(self.root) / "default.gdb")
            )
        with self.assertRaisesRegex(RuntimeError, "default toolbox"):
            project_catalog.remove_toolbox(
                self.project, str(Path(self.root) / "default.atbx")
            )

    def test_styles_and_style_items(self) -> None:
        added = project_catalog.add_style(self.project, "Pushpins")
        self.assertTrue(added["verified"])
        items = project_catalog.list_style_items(
            self.project, "ArcGIS 2D", "POINT", "Airport*"
        )
        self.assertEqual(items["items"][0]["name"], "Airport")
        removed = project_catalog.remove_style(self.project, "Pushpins")
        self.assertTrue(removed["removed"])

    def test_path_outside_input_roots_is_rejected(self) -> None:
        outside = str(Path(self.root).parent / "outside-catalog")
        with self.assertRaisesRegex(RuntimeError, "INPUT_ROOTS"):
            project_catalog.add_folder_connection(self.project, outside)


if __name__ == "__main__":
    unittest.main()
