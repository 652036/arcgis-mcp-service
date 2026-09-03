from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import lidar


class Result:
    def getMessages(self):
        return "done"


class Management:
    def __init__(self, owner):
        self.owner = owner
        self.calls = []

    def CreateLasDataset(self, *args):
        self.calls.append(("create", args))
        self.owner.created.add(args[1])
        return Result()

    def LasDatasetStatistics(self, *args):
        self.calls.append(("statistics", args))
        if args[2] != "#":
            with open(args[2], "w", encoding="utf-8") as stream:
                stream.write("statistics")
        self.owner.has_statistics = True
        return Result()

    def BuildLasDatasetPyramid(self, *args):
        self.calls.append(("pyramid", args))
        self.owner.has_pyramid = True
        return Result()


class FakeArcpy:
    def __init__(self, las_dataset):
        self.created = {las_dataset}
        self.has_statistics = False
        self.has_pyramid = False
        self.management = Management(self)
        self.Extent = lambda *values: tuple(values)
        self.SpatialReference = lambda wkid: ("wkid", wkid)

    def Exists(self, path):
        return path in self.created

    def Describe(self, _path):
        return SimpleNamespace(
            dataType="LasDataset",
            fileCount=2,
            pointCount=42,
            constraintCount=0,
            hasStatistics=self.has_statistics,
            needsUpdateStatistics=not self.has_statistics,
            hasPyramid=self.has_pyramid,
            needsUpdatePyramid=not self.has_pyramid,
            spatialReference=SimpleNamespace(name="WGS 1984", factoryCode=4326),
            extent=SimpleNamespace(XMin=0, YMin=1, XMax=2, YMax=3),
        )


class LidarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.lasd = os.path.join(self.temp.name, "source.lasd")
        self.arcpy = FakeArcpy(self.lasd)
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": self.temp.name,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": self.temp.name,
                "ARCGIS_PRO_MCP_EXPORT_ROOT": self.temp.name,
            },
            clear=False,
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_info_is_typed(self):
        result = lidar.las_dataset_info(self.arcpy, self.lasd)
        self.assertEqual(result["point_count"], 42)
        self.assertEqual(result["spatial_reference"]["wkid"], 4326)

    def test_create_validates_constants_and_verifies_output(self):
        source = os.path.join(self.temp.name, "tiles")
        output = os.path.join(self.temp.name, "products", "tiles.lasd")
        result = lidar.create_las_dataset(
            self.arcpy,
            [source],
            output,
            recurse_folders=True,
            spatial_reference_wkid=4326,
            relative_paths=True,
            processing_extent=[0, 0, 10, 10],
            contained_files_only=True,
        )
        self.assertEqual(result["input_count"], 1)
        args = self.arcpy.management.calls[-1][1]
        self.assertEqual(args[2], "RECURSION")
        self.assertEqual(args[6], "RELATIVE_PATHS")
        self.assertEqual(args[10], "CONTAINED_FILES")

    def test_create_all_prj_requires_fixed_confirmation(self):
        output = os.path.join(self.temp.name, "all.lasd")
        with self.assertRaisesRegex(RuntimeError, "confirm_all_las_prj"):
            lidar.create_las_dataset(
                self.arcpy,
                [os.path.join(self.temp.name, "tiles")],
                output,
                spatial_reference_wkid=4490,
                create_las_prj="ALL_FILES",
            )

    def test_statistics_requires_exact_target_and_report_is_verified(self):
        with self.assertRaisesRegex(RuntimeError, "精确回显"):
            lidar.calculate_las_statistics(
                self.arcpy,
                self.lasd,
                expected_las_dataset=os.path.join(self.temp.name, "other.lasd"),
            )
        report = os.path.join(self.temp.name, "reports", "las.txt")
        result = lidar.calculate_las_statistics(
            self.arcpy,
            self.lasd,
            expected_las_dataset=self.lasd,
            out_report=report,
        )
        self.assertTrue(result["has_statistics"])
        self.assertGreater(result["report_size_bytes"], 0)

    def test_statistics_overwrite_requires_destructive_confirmation(self):
        with self.assertRaisesRegex(RuntimeError, "confirm_overwrite"):
            lidar.calculate_las_statistics(
                self.arcpy,
                self.lasd,
                expected_las_dataset=self.lasd,
                calculation_type="OVERWRITE_EXISTING_STATS",
            )

    def test_pyramid_class_weights_are_typed(self):
        result = lidar.build_las_pyramid(
            self.arcpy,
            self.lasd,
            expected_las_dataset=self.lasd,
            point_selection_method="CLASS_CODE",
            class_code_weights=[{"class_code": 2, "weight": 100}],
        )
        self.assertTrue(result["has_pyramid"])
        args = self.arcpy.management.calls[-1][1]
        self.assertEqual(args[2], [[2, 100]])

    def test_pyramid_rejects_weights_for_other_method(self):
        with self.assertRaisesRegex(RuntimeError, "仅适用于"):
            lidar.build_las_pyramid(
                self.arcpy,
                self.lasd,
                expected_las_dataset=self.lasd,
                point_selection_method="Z_MIN",
                class_code_weights=[{"class_code": 2, "weight": 100}],
            )


if __name__ == "__main__":
    unittest.main()
