from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import symbology


class _Symbology:
    def __init__(self) -> None:
        self.renderer = SimpleNamespace(
            type="SimpleRenderer",
            symbol=_Symbol(),
        )

    def updateColorizer(self, name: str) -> None:
        if name == "RasterStretchColorizer":
            self.colorizer = SimpleNamespace(type=name)
        elif name == "RasterClassifyColorizer":
            self.colorizer = SimpleNamespace(type=name, classBreaks=[])
        elif name == "RasterUniqueValueColorizer":
            self.colorizer = SimpleNamespace(type=name, groups=[])
        else:
            raise AssertionError(name)
        if hasattr(self, "renderer"):
            del self.renderer


class _Symbol:
    name = "Default"

    def applySymbolFromGallery(self, wildcard: str, index: int) -> None:
        self.name = f"{wildcard}:{index}"


class _Layer:
    def __init__(self) -> None:
        self.symbology = _Symbology()


class ExtendedSymbologyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.write_gate = patch.dict(
            os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=True
        )
        self.write_gate.start()
        self.project = SimpleNamespace(
            listColorRamps=lambda wildcard: [SimpleNamespace(name=wildcard)]
        )

    def tearDown(self) -> None:
        self.write_gate.stop()

    def test_stretch_colorizer_is_typed_and_reported(self) -> None:
        layer = _Layer()
        result = symbology.set_raster_stretch_colorizer(
            self.project,
            layer,
            stretch_type="PercentClip",
            gamma=2,
            min_percent=1,
            max_percent=1,
            color_ramp_name="Bathymetry #2",
        )
        self.assertEqual(result["type"], "RasterStretchColorizer")
        self.assertEqual(result["stretchType"], "PercentClip")
        self.assertEqual(result["gamma"], 2.0)

    def test_classify_and_unique_value_colorizers(self) -> None:
        layer = _Layer()
        classified = symbology.set_raster_classify_colorizer(
            self.project,
            layer,
            "Value",
            break_count=7,
            color_ramp_name="Cyan to Purple",
        )
        self.assertEqual(classified["breakCount"], 7)
        layer = _Layer()
        unique = symbology.set_raster_unique_value_colorizer(
            self.project, layer, "Class_name"
        )
        self.assertEqual(unique["field"], "Class_name")

    def test_apply_gallery_symbol_requires_simple_symbol(self) -> None:
        layer = _Layer()
        result = symbology.apply_gallery_symbol(layer, "Airport", 1)
        self.assertEqual(result["symbol_name"], "Airport:1")

    def test_invalid_stretch_values_fail_before_assignment(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "gamma"):
            symbology.set_raster_stretch_colorizer(
                self.project,
                _Layer(),
                gamma=0,
            )

    def test_label_font_updates_cim_text_symbol_and_inner_color_layer(self) -> None:
        color = SimpleNamespace(values=[])
        text_symbol = SimpleNamespace(
            fontFamilyName="Arial",
            fontStyleName="Regular",
            height=10.0,
            symbol=SimpleNamespace(
                symbolLayers=[SimpleNamespace(color=color)],
            ),
        )
        text_symbol_reference = SimpleNamespace(symbol=text_symbol)
        label_class = SimpleNamespace(name="Default")
        cim = SimpleNamespace(
            labelClasses=[
                SimpleNamespace(name="Default", textSymbol=text_symbol_reference)
            ],
        )
        layer = SimpleNamespace(
            listLabelClasses=lambda: [label_class],
            getDefinition=lambda version: cim,
            setDefinition=lambda value: setattr(layer, "written_cim", value),
        )
        with patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_CIM_WRITE": "1",
            },
            clear=True,
        ):
            symbology.set_label_font(
                object(),
                layer,
                font_name="Aptos",
                font_size=12,
                font_color="1,2,3",
                bold=True,
                italic=True,
            )
        self.assertEqual(text_symbol.fontFamilyName, "Aptos")
        self.assertEqual(text_symbol.height, 12.0)
        self.assertEqual(text_symbol.fontStyleName, "Bold Italic")
        self.assertEqual(color.values, [1, 2, 3, 100])
        self.assertIs(layer.written_cim, cim)


if __name__ == "__main__":
    unittest.main()
