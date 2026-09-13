# Analysis quality workflow

Prefer `arcgis_pro_analysis_asset_info` → `arcgis_pro_gp_clip_raster_checked` → `arcgis_pro_analysis_result_status` for supported clipping. Read [implementation and limits](../../../docs/GIS_RELIABILITY.md).

Register physical source, independent grid reference, and reporting AOI before running. Declare the boundary identity, clip mode, and coverage policy explicitly. For rectangles provide CRS WKT. Preserve the run UUID before submitting. If the request times out, inspect that UUID; do not submit a replacement UUID to blindly repeat work.

The checked adapter currently supports north-up single-band GeoTIFF and readable polygon files. It rejects GUI selection references, rotated grids, multiband and implicit reprojection. It checks full expected-domain coverage, not output-intersection coverage. Missing-data allowances require a reason before execution. Do not use file existence, a renderer, output class min/max, or a successful GP call as scientific acceptance.

Only `execution_status=SUCCEEDED`, `qa_status=PASSED`, and current server-computed `eligible_for_downstream=true` qualify this checked stage. Re-read status before reuse. Scientific suitability is separately NOT_ASSESSED. Legacy tools are not all connected to this gate; do not claim global downstream enforcement.

For raster calculator use explicit variable bindings; never embed filesystem reads or arbitrary Python. Reclassify supports missing_values=ERROR to reject unmapped valid input, remap_mode=RANGE or VALUE. ERROR's input scanner has the same GeoTIFF scope. Environment contracts distinguish UNSET, CLEAR, and VALUE; legacy null still means unset.
