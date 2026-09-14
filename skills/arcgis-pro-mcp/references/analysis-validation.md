# Analysis quality workflow

Prefer `arcgis_pro_analysis_asset_info` → `arcgis_pro_gp_clip_raster_checked` → `arcgis_pro_analysis_result_status` for supported clipping. Read [implementation and limits](https://github.com/652036/arcgis-mcp-service/blob/main/docs/GIS_RELIABILITY.md). This repository link also works when the skill is installed separately or accessed through a client junction.

Configure `ARCGIS_PRO_MCP_INPUT_ROOTS` before using this workflow when inputs must be confined to approved directories. The shared input policy accepts any absolute path when roots are unset; the checked adapter does not impose a separate input-root requirement. A configured GP output root is mandatory.

Register physical source, independent grid reference, and reporting AOI before running. Declare the boundary identity, clip mode, and coverage policy explicitly. For rectangles provide CRS WKT. Preserve the run UUID before submitting. If the request times out, inspect that UUID; do not submit a replacement UUID to blindly repeat work.

The checked adapter currently supports north-up single-band GeoTIFF and readable polygon files. It rejects GUI selection references, rotated grids, multiband and implicit reprojection. It checks full expected-domain coverage, not output-intersection coverage. Missing-data allowances require a reason before execution. Do not use file existence, a renderer, output class min/max, or a successful GP call as scientific acceptance.

Only `execution_status=SUCCEEDED`, `qa_status=PASSED`, and current server-computed `eligible_for_downstream=true` qualify this checked stage. Re-read status before reuse. Scientific suitability is separately NOT_ASSESSED. Legacy tools are not all connected to this gate; do not claim global downstream enforcement.

For raster calculator use explicit variable bindings; never embed filesystem reads or arbitrary Python. Migrate `Raster("C:/allowed/input.tif") * 2` to `expression="x * 2"`, `input_rasters={"x": "C:/allowed/input.tif"}` using your allowed absolute path. Reclassify supports missing_values=ERROR to reject unmapped valid input, remap_mode=RANGE or VALUE. ERROR's input scanner has the same GeoTIFF scope and requires reclass_field=Value (case-insensitive); DATA/NODATA can still use raster attribute fields. Environment contracts distinguish UNSET, CLEAR, and VALUE; legacy null still means unset.

Run IDs must be canonical lower-case UUIDs. Assets must include nonempty string fields `asset_id`, `path`, `role`, `identity_basis`, and `fingerprint`; metadata is always re-read. Omit `validity` for RAW defaults, or explicitly supply `value_space=RAW|PHYSICAL`; an empty policy is invalid.
