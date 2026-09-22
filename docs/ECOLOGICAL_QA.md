# Ecological workflow acceptance

[中文版](ECOLOGICAL_QA.zh-CN.md)

## Weighted Overlay through MCP

`arcgis_pro_gp_run_tool` supports `sa.WeightedOverlay` and `WeightedOverlay_sa`.
Use a structured value table, which the server converts to native ArcPy
`WOTable` / `RemapValue` objects before calling the registered GP tool:

```json
{
  "tool_name": "sa.WeightedOverlay",
  "parameters": {
    "in_weighted_overlay_table": {
      "evaluation_scale": [1, 9, 1],
      "rasters": [
        {
          "raster": "C:/GIS/input/landuse.tif",
          "influence": 100,
          "field": "Value",
          "remap": [[1, 5], [2, 1], [5, 2], [8, 9]]
        }
      ]
    },
    "out_raster": "C:/GIS/output/resistance.tif"
  }
}
```

Replace paths and remaps with the declared inputs and analysis model. Each
embedded raster is checked against the configured input roots; the ordinary
new-output gate remains in force. Influences must be integer percentages that
sum to 100. The scale is `[minimum, maximum, increment]`; numeric remap outputs
must belong to it. `NODATA` and `RESTRICTED` are explicit native special values.
This adapter supports integer input-value pairs, not ranges or textual attribute
remaps. Duplicate input values, unknown JSON fields and encoded native table
strings are rejected. Paths containing a single quote, semicolon or newline are
rejected because the native compound parameter cannot safely quote them.

For `TabulateArea_sa`, inspect the actual zone/class fields before calling it.
Use `Value` for pixel classes when that field exists. Do not substitute a made-up
`GROUP_ID` field or silently fall back from a requested attribute field. Supply
the processing cell size explicitly and verify the resulting area totals.

## Reproducible local-data acceptance

`tests/test_ecological_workflow_native.py` is an opt-in integration test that
launches the configured **stdio MCP server**. It copies a bounded local CLCD
land-use GeoTIFF and its sidecars to a new output directory, then runs native GP
through MCP. The input must be a projected metre grid with square cells and no
more than one million cells. Class 2 supplies forest patches; the two largest
8-connected patches are selected. The two declared remaps and 60/40 weights are
a test model, not recommended research weights or evidence of ecological source
suitability. No MSPA, InVEST, Circuitscape or connectivity-index implementation is
implied.

The test checks each stage before progressing:

- source selection and connected components against independent NumPy/SciPy results;
- every resistance pixel, its mask, CRS, cell size and origin;
- both cost-distance rasters against an independent 8-neighbour Dijkstra solver;
- zero costs at sources, corridor sums, connected path endpoints and descending backlinks;
- every area-table entry, total area, raster-to-polygon area, zonal count/mean/sum;
- rejection of invalid weights and nonexistent class fields;
- no downstream MCP request after injected half-cell shifts, NoData gaps,
  wrong values or nonpositive resistance;
- unchanged original input-family SHA-256 hashes.

Run from the repository with ArcGIS Pro Python and compatible MCP dependencies:

```powershell
$previousPythonPath = $env:PYTHONPATH
try {
    $env:PYTHONPATH = (Resolve-Path .arcgis-pro-mcp-deps).Path
    $env:ARCGIS_MCP_RUN_ECOLOGY_QA = "1"
    $env:ARCGIS_MCP_ECOLOGY_CONFIG = (Resolve-Path .mcp.json).Path
    $env:ARCGIS_MCP_ECOLOGY_LANDUSE = "C:\GIS\input\landuse.tif"
    $env:ARCGIS_MCP_ECOLOGY_OUTPUT_ROOT = "C:\GIS\qa\new-acceptance-run"
    & "C:\Program Files\ArcGIS\Pro\bin\Python\Scripts\propy.bat" -m unittest tests.test_ecological_workflow_native -v
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Remove-Item Env:ARCGIS_MCP_RUN_ECOLOGY_QA -ErrorAction SilentlyContinue
}
```

The output directory must not already exist. A scoped server subprocess uses
that exact directory as its input and GP output roots. Client config, original
datasets and CURRENT projects are not changed. The retained `acceptance.json`
contains stage checks, requests, errors and runtime identity; `mcp.log` contains
native diagnostics. A failed check terminates the workflow and leaves a FAILED
report. Expected rejection cases are recorded separately as successful checks.
These local artifacts contain absolute paths and belong outside version control.

Passing this test establishes the stated model/data/environment contract only.
Its sequential failure gates belong to this acceptance runner; they do not add a
global gate to every legacy MCP tool. Full production datasets and different
source definitions, resistance models, licenses or environments need their own
acceptance runs. Restart an existing MCP process before using updated source.

## Native references

- [Esri WOTable](https://pro.arcgis.com/en/pro-app/3.6/arcpy/spatial-analyst/wotable-class.htm)
- [Esri Weighted Overlay](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/spatial-analyst/weighted-overlay.htm)
- [Esri Tabulate Area](https://pro.arcgis.com/en/pro-app/3.6/tool-reference/spatial-analyst/tabulate-area.htm)
