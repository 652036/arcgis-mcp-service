from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from arcgis_pro_mcp import charts


class FakeField:
    def __init__(self, name) -> None:
        self.name = name


class FakeAxis:
    def __init__(self) -> None:
        self.title = ""


class FakeChart:
    def __init__(self, chart_type, **kwargs) -> None:
        self.type = chart_type.lower()
        self.title = ""
        self.description = ""
        self.dataSource = None
        self.theme = "Light"
        self.x = None
        self.y = None
        self.categoryField = None
        self.numberFields = None
        self.aggregation = None
        self.splitCategory = None
        self.rotated = False
        self.showTrendLine = False
        self.binCount = None
        self.showMean = False
        self.showMedian = False
        self.showStandardDeviation = False
        self.donutSize = 0
        self.groupingPercent = 0
        self.showDataLabels = False
        self.xAxis = FakeAxis()
        self.yAxis = FakeAxis()
        self.update_count = 0
        self.export_calls = []
        for key, value in kwargs.items():
            setattr(self, key, value)

    def addToLayer(self, target):
        target.charts.append(self)

    def updateChart(self):
        self.update_count += 1

    def _export(self, output_path, width, height, output_format):
        self.export_calls.append((output_format, output_path, width, height))
        Path(output_path).write_bytes(output_format.encode("ascii"))

    def exportToSVG(self, output_path, width, height):
        self._export(output_path, width, height, "SVG")

    def exportToPNG(self, output_path, width, height):
        self._export(output_path, width, height, "PNG")

    def exportToJPEG(self, output_path, width, height):
        self._export(output_path, width, height, "JPEG")


class FakeChartsModule:
    def __init__(self) -> None:
        self.calls = []

    def _new(self, chart_type, kwargs):
        self.calls.append((chart_type, kwargs))
        return FakeChart(chart_type, **kwargs)

    def Bar(self, **kwargs):
        return self._new("BAR", kwargs)

    def Line(self, **kwargs):
        return self._new("LINE", kwargs)

    def Scatter(self, **kwargs):
        return self._new("SCATTER", kwargs)

    def Histogram(self, **kwargs):
        return self._new("HISTOGRAM", kwargs)

    def Pie(self, **kwargs):
        return self._new("PIE", kwargs)


class FakeTarget:
    def __init__(self) -> None:
        self.charts = []
        self.fields = [
            FakeField("CATEGORY"),
            FakeField("DATE"),
            FakeField("VALUE"),
            FakeField("VALUE_2"),
            FakeField("GROUP"),
        ]

    def listCharts(self):
        return list(self.charts)


class FakeArcpy:
    def __init__(self) -> None:
        self.charts = FakeChartsModule()

    @staticmethod
    def ListFields(target):
        return target.fields


class ChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.env = patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_EXPORT_ROOT": self.temp_dir.name,
            },
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_create_typed_bar_chart_and_canonicalize_fields(self):
        arcpy = FakeArcpy()
        target = FakeTarget()
        result = charts.upsert_chart(
            arcpy,
            target,
            "bar",
            "Values by category",
            x="category",
            y=["value", "value_2"],
            aggregation="sum",
            split_category="group",
            x_title="Category",
            y_title="Total",
            rotated=True,
        )
        self.assertTrue(result["created"])
        chart = result["chart"]
        self.assertEqual(chart.x, "CATEGORY")
        self.assertEqual(chart.y, ["VALUE", "VALUE_2"])
        self.assertEqual(chart.aggregation, "SUM")
        self.assertEqual(chart.splitCategory, "GROUP")
        self.assertTrue(chart.rotated)
        self.assertEqual(chart.xAxis.title, "Category")
        self.assertEqual(target.charts, [chart])

    def test_update_existing_scatter_chart_calls_update(self):
        arcpy = FakeArcpy()
        target = FakeTarget()
        existing = FakeChart("SCATTER", title="Relationship", x="VALUE", y="VALUE_2")
        target.charts.append(existing)
        result = charts.upsert_chart(
            arcpy,
            target,
            "SCATTER",
            "Relationship",
            x="VALUE_2",
            y="VALUE",
            show_trend_line=True,
            theme="Dark",
        )
        self.assertFalse(result["created"])
        self.assertEqual(existing.update_count, 1)
        self.assertEqual(existing.x, "VALUE_2")
        self.assertTrue(existing.showTrendLine)
        self.assertEqual(existing.theme, "Dark")

    def test_same_title_with_different_type_fails_without_deleting(self):
        target = FakeTarget()
        existing = FakeChart("BAR", title="Existing", x="CATEGORY")
        target.charts.append(existing)
        with self.assertRaisesRegex(RuntimeError, "没有安全的图表删除 API"):
            charts.upsert_chart(
                FakeArcpy(),
                target,
                "LINE",
                "Existing",
                x="DATE",
                y="VALUE",
            )
        self.assertEqual(target.charts, [existing])

    def test_line_histogram_and_pie_use_typed_constructors(self):
        cases = [
            (
                "LINE",
                {"x": "DATE", "y": "VALUE", "aggregation": "MEAN"},
                lambda chart: chart.y == "VALUE",
            ),
            (
                "HISTOGRAM",
                {"x": ["VALUE", "VALUE_2"], "bin_count": 20, "show_median": True},
                lambda chart: chart.binCount == 20 and chart.showMedian,
            ),
            (
                "PIE",
                {
                    "category_field": "CATEGORY",
                    "number_fields": ["VALUE"],
                    "donut_size": 35,
                    "show_data_labels": True,
                },
                lambda chart: chart.categoryField == "CATEGORY" and chart.donutSize == 35,
            ),
        ]
        for chart_type, kwargs, assertion in cases:
            with self.subTest(chart_type=chart_type):
                arcpy = FakeArcpy()
                target = FakeTarget()
                result = charts.upsert_chart(arcpy, target, chart_type, f"{chart_type} chart", **kwargs)
                self.assertTrue(assertion(result["chart"]))
                self.assertEqual(arcpy.charts.calls[0][0], chart_type)

    def test_invalid_field_and_type_specific_property_fail_closed(self):
        with self.assertRaisesRegex(RuntimeError, "字段不存在"):
            charts.upsert_chart(
                FakeArcpy(),
                FakeTarget(),
                "BAR",
                "Bad field",
                x="MISSING",
            )
        with self.assertRaisesRegex(RuntimeError, "仅适用于 SCATTER"):
            charts.upsert_chart(
                FakeArcpy(),
                FakeTarget(),
                "BAR",
                "Bad option",
                x="CATEGORY",
                show_trend_line=True,
            )

    def test_list_and_export_chart_to_controlled_path(self):
        target = FakeTarget()
        chart = FakeChart("BAR", title="Export me", x="CATEGORY", y="VALUE")
        target.charts.append(chart)
        listed = charts.list_charts(target)
        self.assertEqual(listed[0]["type"], "BAR")
        output = os.path.join(self.temp_dir.name, "charts", "bar.png")
        result = charts.export_chart(target, "Export me", output, width=900, height=500)
        self.assertEqual(result["format"], "PNG")
        self.assertEqual(chart.export_calls, [("PNG", output, 900, 500)])
        outside = os.path.join(os.path.dirname(self.temp_dir.name), "outside.svg")
        with self.assertRaisesRegex(RuntimeError, "EXPORT_ROOT"):
            charts.export_chart(target, "Export me", outside)

    def test_export_chart_refuses_to_overwrite_existing_file(self):
        target = FakeTarget()
        chart = FakeChart("BAR", title="Keep", x="CATEGORY", y="VALUE")
        target.charts.append(chart)
        output = Path(self.temp_dir.name, "existing.png")
        output.write_bytes(b"original")
        with self.assertRaisesRegex(RuntimeError, "拒绝隐式覆盖"):
            charts.export_chart(target, "Keep", str(output))
        self.assertEqual(output.read_bytes(), b"original")
        self.assertEqual(chart.export_calls, [])

    def test_write_gate_applies_to_upsert_and_export(self):
        target = FakeTarget()
        target.charts.append(FakeChart("BAR", title="Chart", x="CATEGORY"))
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "0"}):
            with self.assertRaisesRegex(RuntimeError, "写入类操作已禁用"):
                charts.upsert_chart(FakeArcpy(), target, "BAR", "New", x="CATEGORY")
            with self.assertRaisesRegex(RuntimeError, "写入类操作已禁用"):
                charts.export_chart(target, "Chart", os.path.join(self.temp_dir.name, "chart.svg"))

    def test_delete_capability_is_explicitly_unsupported(self):
        capability = charts.chart_delete_capability()
        self.assertFalse(capability["supported"])
        self.assertFalse(hasattr(charts, "delete_chart"))


if __name__ == "__main__":
    unittest.main()
