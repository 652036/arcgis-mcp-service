"""Opt-in, real stdio MCP acceptance of a bounded local-data MCR workflow.

Run with Pro Python, ARCGIS_MCP_RUN_ECOLOGY_QA=1, and the explicit config,
land-use GeoTIFF and new report directory described in ECOLOGICAL_QA.md.
No original dataset, CURRENT project, or client configuration is modified.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import heapq
import json
import math
import os
import shutil
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from arcgis_pro_mcp.analysis_quality import grid_offset


class QualityFailure(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise QualityFailure(message)


def dijkstra(cost, valid, sources, cell_size):
    """Independent 8-neighbour arithmetic-mean cost oracle; no ArcPy algorithms."""
    import numpy as np

    rows, cols = cost.shape
    distances = np.full(cost.shape, np.inf)
    queue = []
    for row, col in np.argwhere(sources):
        row, col = int(row), int(col)
        distances[row, col] = 0.0
        heapq.heappush(queue, (0.0, row, col))
    moves = [(dy, dx, math.hypot(dy, dx) * cell_size)
             for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dy or dx]
    while queue:
        distance, row, col = heapq.heappop(queue)
        if distance != distances[row, col]:
            continue
        for dy, dx, length in moves:
            y, x = row + dy, col + dx
            if 0 <= y < rows and 0 <= x < cols and valid[y, x]:
                candidate = distance + (float(cost[row, col]) + float(cost[y, x])) * 0.5 * length
                if candidate < distances[y, x]:
                    distances[y, x] = candidate
                    heapq.heappush(queue, (candidate, y, x))
    return distances


@unittest.skipUnless(os.environ.get("ARCGIS_MCP_RUN_ECOLOGY_QA") == "1", "explicit local-data native QA only")
class EcologicalWorkflowNativeTests(unittest.TestCase):
    def test_local_data_mcp_workflow_and_failure_gates(self):
        import arcpy
        import numpy as np
        from osgeo import gdal, osr

        gdal.UseExceptions()
        self.arcpy, self.np, self.gdal, self.osr = arcpy, np, gdal, osr
        self.repo = Path(__file__).resolve().parents[1]
        self.config_path = Path(os.environ["ARCGIS_MCP_ECOLOGY_CONFIG"])
        self.source = Path(os.environ["ARCGIS_MCP_ECOLOGY_LANDUSE"])
        self.root = Path(os.environ["ARCGIS_MCP_ECOLOGY_OUTPUT_ROOT"])
        require(all(p.is_absolute() for p in (self.config_path, self.source, self.root)), "absolute paths required")
        self.root.mkdir(parents=True, exist_ok=False)
        self.records, self.calls = [], []
        self.report = {"status": "RUNNING", "checks": self.records, "calls": self.calls,
                       "scope": "local-data numeric MCR acceptance; test model, not ecological model approval",
                       "started_at": datetime.now(timezone.utc).isoformat()}
        family = [self.source] + [p for p in self.source.parent.glob(self.source.name + ".*") if p.is_file()]
        self.hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in family}
        inputs = self.root / "inputs"
        inputs.mkdir()
        for p in family:
            shutil.copy2(p, inputs / p.name)
        self.landuse = inputs / self.source.name
        try:
            asyncio.run(self.workflow())
            unchanged = all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in self.hashes.items())
            self.check("original_input_family_unchanged", unchanged, files=len(self.hashes))
            self.report["status"] = "PASSED"
        except BaseException as ex:
            self.report.update(status="FAILED", error=str(ex))
            raise
        finally:
            self.report["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.save_report()

    def save_report(self):
        (self.root / "acceptance.json").write_text(json.dumps(self.report, indent=2, ensure_ascii=False), encoding="utf-8")

    def check(self, name, condition, **metrics):
        self.records.append({"name": name, "status": "PASSED" if condition else "FAILED", **metrics})
        self.save_report()
        require(bool(condition), name)
        print(f"QA {name}: PASSED", flush=True)

    def raster(self, path):
        ds = self.gdal.Open(str(path))
        require(ds is not None and ds.RasterCount == 1, "single-band readable raster required")
        require(0 < ds.RasterXSize * ds.RasterYSize <= 1_000_000, "QA sample exceeds one million cells")
        gt = ds.GetGeoTransform()
        require(gt[1] > 0 and gt[5] < 0 and gt[2] == gt[4] == 0, "north-up grid required")
        array = ds.ReadAsArray()
        mask = (ds.GetRasterBand(1).GetMaskBand().ReadAsArray() != 0) & self.np.isfinite(array)
        result = {"array": array, "mask": mask, "wkt": ds.GetProjection(), "grid": {
            "x0": gt[0], "y0": gt[3], "dx": gt[1], "dy": -gt[5],
            "rows": ds.RasterYSize, "cols": ds.RasterXSize,
        }}
        ds = None
        return result

    def aligned(self, actual, reference):
        a, b = self.osr.SpatialReference(wkt=actual["wkt"]), self.osr.SpatialReference(wkt=reference["wkt"])
        require(bool(a.IsSame(b)), "CRS_MISMATCH")
        try:
            grid_offset(reference["grid"], actual["grid"], same_window=True)
        except RuntimeError as ex:
            raise QualityFailure(str(ex)) from ex

    def resistance_gate(self, raster, reference, expected):
        self.aligned(raster, reference)
        require(self.np.array_equal(raster["mask"], reference["mask"]), "RESISTANCE_NODATA_MISMATCH")
        values = raster["array"][raster["mask"]]
        require(bool((values > 0).all()), "NONPOSITIVE_RESISTANCE")
        require(self.np.array_equal(values, expected[raster["mask"]]), "RESISTANCE_VALUE_MISMATCH")

    async def call(self, name, arguments):
        result = await self.session.call_tool(name, arguments)
        payload = result.structuredContent
        if payload is None:
            text = "\n".join(c.text for c in result.content if c.type == "text")
            try:
                payload = json.loads(text)
            except ValueError:
                payload = {"message": text}
        self.calls.append({"tool": name, "arguments": arguments, "is_error": bool(result.isError), "result": payload})
        self.save_report()
        return result.isError, payload

    async def gp(self, name, arguments, gates=()):
        # A failed prerequisite raises before the MCP request is submitted.
        for gate in gates:
            gate()
        error, payload = await self.call("arcgis_pro_gp_run_tool", {"tool_name": name, "parameters": arguments})
        require(not error, f"{name}: {payload}")
        return payload

    async def workflow(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        cfg = json.loads(self.config_path.read_text(encoding="utf-8-sig"))["mcpServers"]["arcgis-pro"]
        env = dict(os.environ, **cfg.get("env", {}))
        env["PYTHONPATH"] = os.pathsep.join([str(self.repo / ".arcgis-pro-mcp-deps"), str(self.repo)])
        env.update(ARCGIS_PRO_MCP_GP_OUTPUT_ROOT=str(self.root), ARCGIS_PRO_MCP_INPUT_ROOTS=str(self.root))
        require(env.get("ARCGIS_PRO_MCP_ALLOW_WRITE", "1").lower() in {"1", "true", "yes", "on"}, "write gate disabled")
        self.report["runtime"] = {"command": cfg["command"], "args": cfg["args"], "cwd": str(self.repo),
                                  "input_roots": str(self.root), "gp_output_root": str(self.root)}
        with (self.root / "mcp.log").open("w", encoding="utf-8") as log:
            params = StdioServerParameters(command=cfg["command"], args=cfg["args"], cwd=str(self.repo), env=env)
            async with stdio_client(params, errlog=log) as streams:
                async with ClientSession(*streams, read_timeout_seconds=timedelta(seconds=300)) as session:
                    self.session = session
                    for name in ("arcgis_pro_environment_info", "arcgis_pro_server_capabilities"):
                        if name.endswith("environment_info"):
                            await session.initialize()
                        error, _ = await self.call(name, {})
                        require(not error, name)
                    error, _ = await self.call("arcgis_pro_tool_info", {"name": "arcgis_pro_gp_run_tool"})
                    require(not error, "tool info unavailable")
                    await self.stages()

    async def stages(self):
        np, arcpy = self.np, self.arcpy
        land = self.raster(self.landuse)
        spatial_ref = self.osr.SpatialReference(wkt=land["wkt"])
        self.check("projected_metre_square_grid", bool(spatial_ref.IsProjected()) and
                   math.isclose(spatial_ref.GetLinearUnits(), 1) and land["grid"]["dx"] == land["grid"]["dy"])
        self.check("bounded_nonempty_local_sample", 0 < int(land["mask"].sum()) <= 1_000_000)
        values = [int(v) for v in np.unique(land["array"][land["mask"]])]
        # Explicit CLCD-class test model: these weights are not scientific defaults.
        mapping_a = {1: 5, 2: 1, 3: 2, 4: 3, 5: 2, 6: 6, 7: 7, 8: 9, 9: 3}
        mapping_b = {1: 6, 2: 2, 3: 3, 4: 4, 5: 1, 6: 6, 7: 7, 8: 9, 9: 4}
        self.check("declared_test_classes", set(values) <= set(mapping_a) and 2 in values, values=values)
        self.report["test_model"] = {"source_class": 2, "source_selection": "two largest 8-connected forest patches",
                                     "influences": [60, 40], "remaps": [mapping_a, mapping_b]}
        source_candidates, groups = self.root / "forest.tif", self.root / "groups.tif"
        await self.gp("Reclassify_sa", {"in_raster": str(self.landuse), "reclass_field": "Value",
                      "remap": ";".join(f"{v} {'1' if v == 2 else 'NODATA'}" for v in values),
                      "out_raster": str(source_candidates), "missing_values": "NODATA"})
        forest = self.raster(source_candidates)
        self.aligned(forest, land)
        expected_forest = land["mask"] & (land["array"] == 2)
        self.check("source_selection_values_and_nodata", np.array_equal(forest["mask"], expected_forest) and
                   bool((forest["array"][forest["mask"]] == 1).all()), cells=int(expected_forest.sum()))
        await self.gp("RegionGroup_sa", {"in_raster": str(source_candidates), "out_raster": str(groups),
                      "number_neighbors": "EIGHT", "zone_connectivity": "WITHIN", "add_link": "NO_LINK"})
        grouped = self.raster(groups)
        self.aligned(grouped, land)
        from scipy.ndimage import label

        labels, count = label(expected_forest, structure=np.ones((3, 3)))
        native_ids, sizes = np.unique(grouped["array"][grouped["mask"]], return_counts=True)
        pairs = np.unique(np.stack([labels[expected_forest], grouped["array"][expected_forest]], axis=1), axis=0)
        self.check("source_components_independent", np.array_equal(grouped["mask"], expected_forest) and
                   len(native_ids) == count == len(pairs), components=int(count))
        selected = native_ids[np.argsort(sizes)[-2:]]
        self.check("two_nonempty_sources", len(selected) == 2)
        all_sources = self.root / "sources.tif"
        await self.gp("Reclassify_sa", {"in_raster": str(groups), "reclass_field": "Value",
                      "remap": f"{int(selected[0])} 1;{int(selected[1])} 2", "out_raster": str(all_sources),
                      "missing_values": "NODATA"})
        sources = self.raster(all_sources)
        self.aligned(sources, land)
        source_masks = [grouped["mask"] & (grouped["array"] == source_id) for source_id in selected]
        self.check("selected_sources_values_and_nodata", np.array_equal(sources["mask"], source_masks[0] | source_masks[1]) and
                   all(bool((sources["array"][m] == i + 1).all()) for i, m in enumerate(source_masks)))
        table = {"evaluation_scale": [1, 9, 1], "rasters": [
            {"raster": str(self.landuse), "influence": weight, "field": "Value",
             "remap": [[v, mapping[v]] for v in values]}
            for weight, mapping in ((60, mapping_a), (40, mapping_b))
        ]}
        resistance_path = self.root / "resistance.tif"
        await self.gp("sa.WeightedOverlay", {"in_weighted_overlay_table": table, "out_raster": str(resistance_path)})
        resistance = self.raster(resistance_path)
        expected = np.zeros(land["array"].shape, dtype=np.int32)
        for value in values:
            expected[land["array"] == value] = math.floor(0.6 * mapping_a[value] + 0.4 * mapping_b[value] + 0.5)
        self.resistance_gate(resistance, land, expected)
        self.check("weighted_overlay_all_cells_grid_mask_values", True, cells=int(land["mask"].sum()))
        invalid = copy.deepcopy(table)
        invalid["rasters"][0]["influence"] = 59
        bad_weight_path = self.root / "rejected_weight.tif"
        error, _ = await self.call("arcgis_pro_gp_run_tool", {"tool_name": "WeightedOverlay_sa", "parameters": {
            "in_weighted_overlay_table": invalid, "out_raster": str(bad_weight_path)}})
        self.check("invalid_weights_rejected_without_output", error and not arcpy.Exists(str(bad_weight_path)))
        for problem in ("alignment", "nodata", "values", "nonpositive"):
            bad = copy.deepcopy(resistance)
            y, x = np.argwhere(land["mask"])[0]
            if problem == "alignment":
                bad["grid"]["x0"] += land["grid"]["dx"] / 2
            elif problem == "nodata":
                bad["mask"][y, x] = False
            else:
                bad["array"][y, x] = 0 if problem == "nonpositive" else 99
            before = len(self.calls)
            try:
                await self.gp("CostDistance_sa", {}, gates=[lambda bad=bad: self.resistance_gate(bad, land, expected)])
            except QualityFailure:
                pass
            else:
                raise QualityFailure(f"bad {problem} was accepted")
            self.check(f"bad_{problem}_blocks_downstream_request", len(self.calls) == before)
        distance_arrays, distance_paths, backlink_paths = [], [], []
        for index, source_mask in enumerate(source_masks, 1):
            source_path = self.root / f"source{index}.tif"
            await self.gp("Reclassify_sa", {"in_raster": str(all_sources), "reclass_field": "Value",
                          "remap": f"{index} 1", "out_raster": str(source_path), "missing_values": "NODATA"})
            distance_path, backlink_path = self.root / f"distance{index}.tif", self.root / f"backlink{index}.tif"
            await self.gp("CostDistance_sa", {"in_source_data": str(source_path), "in_cost_raster": str(resistance_path),
                          "out_distance_raster": str(distance_path), "out_backlink_raster": str(backlink_path)},
                          gates=[lambda: self.resistance_gate(self.raster(resistance_path), land, expected)])
            distance = self.raster(distance_path)
            self.aligned(distance, land)
            oracle = dijkstra(expected, land["mask"], source_mask, land["grid"]["dx"])
            reachable = np.isfinite(oracle)
            self.check(f"distance{index}_coverage", np.array_equal(distance["mask"], reachable))
            max_error = float(np.max(np.abs(distance["array"][reachable] - oracle[reachable])))
            self.check(f"distance{index}_independent_dijkstra", np.allclose(distance["array"][reachable], oracle[reachable],
                       rtol=5e-6, atol=0.02), max_absolute_error=max_error, checked_cells=int(reachable.sum()))
            self.check(f"source{index}_zero_cost", bool((distance["array"][source_mask] == 0).all()))
            distance_arrays.append(distance)
            distance_paths.append(distance_path)
            backlink_paths.append(backlink_path)
        corridor_path = self.root / "corridor.tif"
        await self.gp("Corridor_sa", {"in_distance_raster1": str(distance_paths[0]),
                      "in_distance_raster2": str(distance_paths[1]), "out_raster": str(corridor_path)})
        corridor = self.raster(corridor_path)
        self.aligned(corridor, land)
        common = distance_arrays[0]["mask"] & distance_arrays[1]["mask"]
        corridor_expected = distance_arrays[0]["array"].astype(float) + distance_arrays[1]["array"]
        self.check("corridor_all_cells_sum_and_mask", np.array_equal(corridor["mask"], common) and
                   np.allclose(corridor["array"][common], corridor_expected[common], rtol=1e-6, atol=0.02))
        path = self.root / "least_cost_path.tif"
        await self.gp("CostPath_sa", {"in_destination_data": str(self.root / "source2.tif"),
                      "in_cost_distance_raster": str(distance_paths[0]), "in_cost_backlink_raster": str(backlink_paths[0]),
                      "out_raster": str(path), "path_type": "BEST_SINGLE", "destination_field": "Value"})
        route = self.raster(path)
        self.aligned(route, land)
        _, components = label(route["mask"], structure=np.ones((3, 3)))
        self.check("path_connects_both_sources", components == 1 and all(bool((route["mask"] & m).any()) for m in source_masks))
        self.check("path_stays_in_valid_cost_domain", not bool((route["mask"] & ~land["mask"]).any()))
        backlink = self.raster(backlink_paths[0])
        self.aligned(backlink, land)
        dist = distance_arrays[0]["array"]
        endpoint = np.where(source_masks[1], dist, np.inf)
        y, x = np.unravel_index(np.argmin(endpoint), endpoint.shape)
        directions = {1: (0, 1), 2: (1, 1), 3: (1, 0), 4: (1, -1),
                      5: (0, -1), 6: (-1, -1), 7: (-1, 0), 8: (-1, 1)}
        visited = set()
        while not source_masks[0][y, x]:
            require((y, x) not in visited and bool(route["mask"][y, x]), "PATH_BACKLINK_MISMATCH")
            visited.add((y, x))
            require(int(backlink["array"][y, x]) in directions, "INVALID_BACKLINK")
            dy, dx = directions[int(backlink["array"][y, x])]
            ny, nx = y + dy, x + dx
            require(0 <= ny < dist.shape[0] and 0 <= nx < dist.shape[1], "BACKLINK_OUTSIDE_GRID")
            require(dist[ny, nx] < dist[y, x], "BACKLINK_NOT_DESCENDING")
            y, x = ny, nx
        self.check("path_follows_descending_backlink_to_source", True, path_steps=len(visited))
        area_path = self.root / "areas.dbf"
        area_args = {"in_zone_data": str(self.landuse), "zone_field": "Value", "in_class_data": str(resistance_path),
                     "class_field": "Value", "out_table": str(area_path), "processing_cell_size": land["grid"]["dx"]}
        self.check("tabulate_area_fields_exist", all(any(f.name.lower() == "value" for f in arcpy.ListFields(str(p)))
                   for p in (self.landuse, resistance_path)))
        invalid = dict(area_args, class_field="GROUP_ID", out_table=str(self.root / "rejected_area.dbf"))
        error, _ = await self.call("arcgis_pro_gp_run_tool", {"tool_name": "TabulateArea_sa", "parameters": invalid})
        self.check("missing_area_field_rejected", error and not arcpy.Exists(invalid["out_table"]))
        await self.gp("TabulateArea_sa", area_args)
        fields = [f.name for f in arcpy.ListFields(str(area_path))]
        class_fields = [f for f in fields if f.upper().startswith("VALUE_")]
        zone_field = next(f for f in fields if f.lower() == "value")
        cell_area = land["grid"]["dx"] * land["grid"]["dy"]
        areas_ok, zones_seen, area_total = True, set(), 0.0
        with arcpy.da.SearchCursor(str(area_path), [zone_field, *class_fields]) as cursor:
            for record in cursor:
                zone = int(record[0])
                zones_seen.add(zone)
                for field, actual in zip(class_fields, record[1:], strict=True):
                    expected_area = int((land["mask"] & (land["array"] == zone) & (expected == int(field[6:]))).sum()) * cell_area
                    areas_ok &= math.isclose(float(actual), expected_area, rel_tol=1e-10, abs_tol=1e-6)
                    area_total += float(actual)
        self.check("area_every_zone_and_class_independent", areas_ok and zones_seen == set(values) and
                   math.isclose(area_total, int(land["mask"].sum()) * cell_area, abs_tol=1e-6), area_m2=area_total)
        polygons = self.root / "sources.shp"
        await self.gp("RasterToPolygon_conversion", {"in_raster": str(all_sources), "out_polygon_features": str(polygons),
                      "simplify": "NO_SIMPLIFY", "raster_field": "Value"})
        with arcpy.da.SearchCursor(str(polygons), ["SHAPE@AREA"]) as cursor:
            polygon_area = sum(row[0] for row in cursor)
        expected_source_area = int(sources["mask"].sum()) * cell_area
        self.check("source_polygon_raster_area_conservation", math.isclose(polygon_area, expected_source_area, abs_tol=1e-5),
                   raster_area_m2=expected_source_area, polygon_area_m2=polygon_area)
        statistics = self.root / "zonal.dbf"
        await self.gp("ZonalStatisticsAsTable_sa", {"in_zone_data": str(all_sources), "zone_field": "Value",
                      "in_value_raster": str(resistance_path), "out_table": str(statistics),
                      "ignore_nodata": "DATA", "statistics_type": "ALL"})
        with arcpy.da.SearchCursor(str(statistics), ["VALUE", "COUNT", "MEAN", "SUM"]) as cursor:
            rows = list(cursor)
        self.check("zonal_count_mean_sum_independent", len(rows) == 2 and {int(r[0]) for r in rows} == {1, 2} and all(
            int(count) == int(source_masks[int(zone)-1].sum()) and
            math.isclose(mean, float(expected[source_masks[int(zone)-1]].mean()), abs_tol=1e-6) and
            math.isclose(total, float(expected[source_masks[int(zone)-1]].sum()), abs_tol=1e-6)
            for zone, count, mean, total in rows))


if __name__ == "__main__":
    unittest.main()
