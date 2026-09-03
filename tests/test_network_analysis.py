from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from arcgis_pro_mcp import network_analysis


class _CountResult:
    def __init__(self, value: int) -> None:
        self.value = value

    def getOutput(self, index: int) -> str:
        return str(self.value)


class _TravelMode:
    type = "AUTOMOBILE"
    impedance = "TravelTime"
    timeAttributeName = "TravelTime"
    distanceAttributeName = "Kilometers"
    uTurnPolicy = "ALLOW_DEAD_ENDS_AND_INTERSECTIONS_ONLY"
    useHierarchy = True
    restrictions = ["Driving an Automobile"]


class _SolveResult:
    def __init__(self, owner: _Arcpy, succeeded: bool, analysis_name: str) -> None:
        self.owner = owner
        self.solveSucceeded = succeeded
        self.analysis_name = analysis_name

    def solverMessages(self, _severity: object) -> list[tuple[str, str]]:
        if self.solveSucceeded:
            return [("Warning", "2 locations were not located")]
        return [("Error", f"No {self.analysis_name} could be generated")]

    def searchCursor(self, output_type: object, fields: list[str]):
        self.owner.search_calls.append((output_type, fields))
        return iter([(0,), (1,), (5,)])

    def export(self, output_type: object, output_path: str) -> None:
        self.owner.exports.append((output_type, output_path))
        if self.owner.materialize_outputs:
            self.owner.outputs.add(os.path.normpath(output_path))


class _Solver:
    def __init__(self, owner: _Arcpy, network: str, analysis_name: str) -> None:
        self.owner = owner
        self.network = network
        self.analysis_name = analysis_name
        self.owner.created_solver_names.append(analysis_name)
        self.travelMode: object | None = None
        self.ignoreInvalidLocations = False
        self.timeOfDay: object | None = None
        self.returnDirections = False
        self.loads: list[tuple[object, str]] = []

    def load(self, input_type: object, path: str) -> None:
        self.loads.append((input_type, path))

    def solve(self) -> _SolveResult:
        self.owner.last_solver = self
        self.owner.solver_history.append(self)
        return _SolveResult(self.owner, self.owner.solve_succeeded, self.analysis_name)


class _Nax:
    TimeUnits = SimpleNamespace(Seconds="Seconds", Minutes="Minutes", Hours="Hours", Days="Days")
    DistanceUnits = SimpleNamespace(
        Feet="Feet",
        Yards="Yards",
        Miles="Miles",
        NauticalMiles="NauticalMiles",
        Meters="Meters",
        Kilometers="Kilometers",
        Inches="Inches",
        Centimeters="Centimeters",
        Millimeters="Millimeters",
        Decimeters="Decimeters",
    )
    TravelDirection = SimpleNamespace(FromFacility="FromFacility", ToFacility="ToFacility")
    TimeZoneUsage = SimpleNamespace(LocalTimeAtLocations="LocalTimeAtLocations", UTC="UTC")
    TimeOfDayUsage = SimpleNamespace(DepartureTime="DepartureTime", ArrivalTime="ArrivalTime")
    RouteShapeType = SimpleNamespace(
        NoLine="NoLine",
        StraightLine="StraightLine",
        TrueShape="TrueShape",
        TrueShapeWithMeasures="TrueShapeWithMeasures",
    )
    LineShapeType = SimpleNamespace(NoLine="NoLine", StraightLine="StraightLine")
    ServiceAreaPolygonCutoffGeometry = SimpleNamespace(Disks="Disks", Rings="Rings")
    ServiceAreaOverlapGeometry = SimpleNamespace(
        Split="Split",
        Overlap="Overlap",
        Dissolve="Dissolve",
    )
    ServiceAreaPolygonDetail = SimpleNamespace(
        Generalized="Generalized",
        Standard="Standard",
        High="High",
    )
    ServiceAreaOutputType = SimpleNamespace(
        Polygons="Polygons",
        Lines="Lines",
        PolygonsAndLines="PolygonsAndLines",
    )
    RouteInputDataType = SimpleNamespace(
        Stops="Stops",
        PointBarriers="PointBarriers",
        LineBarriers="LineBarriers",
        PolygonBarriers="PolygonBarriers",
    )
    RouteOutputDataType = SimpleNamespace(
        Routes="Routes",
        Stops="Stops",
        Directions="Directions",
    )
    ServiceAreaInputDataType = SimpleNamespace(
        Facilities="Facilities",
        PointBarriers="PointBarriers",
        LineBarriers="LineBarriers",
        PolygonBarriers="PolygonBarriers",
    )
    ServiceAreaOutputDataType = SimpleNamespace(
        Facilities="Facilities",
        Lines="Lines",
        Polygons="Polygons",
    )
    ClosestFacilityInputDataType = SimpleNamespace(
        Incidents="Incidents",
        Facilities="Facilities",
        PointBarriers="PointBarriers",
        LineBarriers="LineBarriers",
        PolygonBarriers="PolygonBarriers",
    )
    ClosestFacilityOutputDataType = SimpleNamespace(
        Incidents="Incidents",
        Facilities="Facilities",
        Routes="Routes",
        Directions="Directions",
    )
    OriginDestinationCostMatrixInputDataType = SimpleNamespace(
        Origins="Origins",
        Destinations="Destinations",
        PointBarriers="PointBarriers",
        LineBarriers="LineBarriers",
        PolygonBarriers="PolygonBarriers",
    )
    OriginDestinationCostMatrixOutputDataType = SimpleNamespace(
        Origins="Origins",
        Destinations="Destinations",
        Lines="Lines",
    )
    MessageSeverity = SimpleNamespace(All="All")

    def __init__(self, owner: _Arcpy) -> None:
        self.owner = owner

    def GetTravelModes(self, network: str) -> dict[str, _TravelMode]:
        self.owner.travel_mode_networks.append(network)
        return {"Driving Time": _TravelMode()}

    def Route(self, network: str) -> _Solver:
        return _Solver(self.owner, network, "route")

    def ServiceArea(self, network: str) -> _Solver:
        return _Solver(self.owner, network, "service area")

    def ClosestFacility(self, network: str) -> _Solver:
        return _Solver(self.owner, network, "closest facility")

    def OriginDestinationCostMatrix(self, network: str) -> _Solver:
        return _Solver(self.owner, network, "OD cost matrix")


class _Arcpy:
    def __init__(
        self,
        *,
        solve_succeeded: bool = True,
        network_data_type: str = "NetworkDataset",
        materialize_outputs: bool = True,
    ) -> None:
        self.solve_succeeded = solve_succeeded
        self.network_data_type = network_data_type
        self.materialize_outputs = materialize_outputs
        self.nax = _Nax(self)
        self.na = SimpleNamespace()
        self.outputs: set[str] = set()
        self.management = SimpleNamespace(
            GetCount=lambda _path: _CountResult(2),
            Delete=lambda path: self.outputs.discard(os.path.normpath(path)),
        )
        self.exports: list[tuple[object, str]] = []
        self.search_calls: list[tuple[object, list[str]]] = []
        self.travel_mode_networks: list[str] = []
        self.extension_events: list[tuple[str, str]] = []
        self.last_solver: _Solver | None = None
        self.solver_history: list[_Solver] = []
        self.created_solver_names: list[str] = []

    def Exists(self, path: str) -> bool:
        normalized = os.path.normpath(path)
        # Treat exported paths as existing; input paths are virtual and exist.
        if normalized in self.outputs:
            return True
        return "results.gdb" not in normalized

    def Describe(self, _path: str) -> SimpleNamespace:
        return SimpleNamespace(dataType=self.network_data_type)

    def CheckExtension(self, name: str) -> str:
        self.extension_events.append(("check", name))
        return "Available"

    def CheckOutExtension(self, name: str) -> str:
        self.extension_events.append(("out", name))
        return "CheckedOut"

    def CheckInExtension(self, name: str) -> str:
        self.extension_events.append(("in", name))
        return "CheckedIn"


class NetworkAnalysisTests(unittest.TestCase):
    def _policy(self, input_root: str, output_root: str):
        return patch.dict(
            os.environ,
            {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
            },
            clear=True,
        )

    def test_list_travel_modes_returns_typed_properties(self) -> None:
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
            clear=True,
        ):
            network = str(Path(root) / "network.gdb" / "Streets_ND")
            result = network_analysis.list_travel_modes(_Arcpy(), network)
        self.assertEqual(result["travel_modes"][0]["name"], "Driving Time")
        self.assertEqual(result["travel_modes"][0]["impedance"], "TravelTime")
        self.assertEqual(result["travel_modes"][0]["restrictions"], ["Driving an Automobile"])

    def test_one_shot_route_solves_exports_and_verifies_results(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            network = str(Path(input_root) / "network.gdb" / "Streets_ND")
            stops = str(Path(input_root) / "stops.shp")
            barriers = str(Path(input_root) / "barriers.shp")
            routes = str(Path(output_root) / "results.gdb" / "Routes")
            output_stops = str(Path(output_root) / "results.gdb" / "Stops")
            directions = str(Path(output_root) / "results.gdb" / "Directions")
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                result = network_analysis.solve_route_once(
                    arcpy,
                    network,
                    stops,
                    "Driving Time",
                    routes,
                    output_stops,
                    directions,
                    point_barriers=barriers,
                    time_of_day="2026-09-03T08:30:00",
                )
        self.assertTrue(result["solve_succeeded"])
        self.assertEqual(result["unlocated_stops"], 2)
        self.assertEqual(set(result["outputs"]), {"routes", "stops", "directions"})
        self.assertEqual([kind for kind, _path in arcpy.exports], ["Routes", "Stops", "Directions"])
        self.assertEqual(
            arcpy.last_solver.loads,
            [("Stops", os.path.normpath(stops)), ("PointBarriers", os.path.normpath(barriers))],
        )
        self.assertEqual(arcpy.last_solver.timeOfDay.isoformat(), "2026-09-03T08:30:00")
        self.assertTrue(arcpy.last_solver.returnDirections)
        self.assertEqual(
            arcpy.extension_events,
            [("check", "Network"), ("out", "Network"), ("in", "Network")],
        )

    def test_failed_solve_raises_and_does_not_export(self) -> None:
        arcpy = _Arcpy(solve_succeeded=False)
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "No route could be generated"):
                    network_analysis.solve_route_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "stops.shp"),
                        "Driving Time",
                        str(Path(output_root) / "results.gdb" / "Routes"),
                        str(Path(output_root) / "results.gdb" / "Stops"),
                    )
        self.assertEqual(arcpy.exports, [])
        self.assertIn(("in", "Network"), arcpy.extension_events)

    def test_unknown_travel_mode_fails_before_solver_creation(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "未找到 travel mode"):
                    network_analysis.solve_route_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "stops.shp"),
                        "Teleport",
                        str(Path(output_root) / "results.gdb" / "Routes"),
                        str(Path(output_root) / "results.gdb" / "Stops"),
                    )
        self.assertIsNone(arcpy.last_solver)

    def test_existing_output_is_rejected_without_explicit_overwrite(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            routes = str(Path(output_root) / "results.gdb" / "Routes")
            arcpy.outputs.add(os.path.normpath(routes))
            with patch.dict(
                os.environ,
                {
                    "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                    "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                    "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "拒绝覆盖"):
                    network_analysis.solve_route_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "stops.shp"),
                        "Driving Time",
                        routes,
                        str(Path(output_root) / "results.gdb" / "Stops"),
                    )

    def test_route_overwrite_requires_both_gate_and_confirmation(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            routes = str(Path(output_root) / "results.gdb" / "Routes")
            stops_out = str(Path(output_root) / "results.gdb" / "Stops")
            arcpy.outputs.add(os.path.normpath(routes))
            environment = {
                "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
                "ARCGIS_PRO_MCP_ALLOW_DESTRUCTIVE": "1",
                "ARCGIS_PRO_MCP_INPUT_ROOTS": input_root,
                "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": output_root,
            }
            with patch.dict(os.environ, environment, clear=True):
                with self.assertRaisesRegex(RuntimeError, "confirm_overwrite"):
                    network_analysis.solve_route_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "stops.shp"),
                        "Driving Time",
                        routes,
                        stops_out,
                        overwrite=True,
                    )
                result = network_analysis.solve_route_once(
                    arcpy,
                    str(Path(input_root) / "network.gdb" / "ND"),
                    str(Path(input_root) / "stops.shp"),
                    "Driving Time",
                    routes,
                    stops_out,
                    overwrite=True,
                    confirm_overwrite=True,
                )
        self.assertTrue(result["solve_succeeded"])
        self.assertIn(os.path.normpath(routes), arcpy.outputs)

    def test_route_requires_write_gate_and_output_root(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "写入类操作已禁用"):
                network_analysis.solve_route_once(
                    arcpy,
                    str(Path(root) / "ND"),
                    str(Path(root) / "stops.shp"),
                    "Driving Time",
                    str(Path(root) / "Routes"),
                    str(Path(root) / "Stops"),
                )

    def test_service_area_sets_typed_properties_loads_and_exports(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            network = str(Path(input_root) / "network.gdb" / "Streets_ND")
            facilities = str(Path(input_root) / "facilities.shp")
            barriers = str(Path(input_root) / "barriers.shp")
            polygons = str(Path(output_root) / "results.gdb" / "ServicePolygons")
            lines = str(Path(output_root) / "results.gdb" / "ServiceLines")
            output_facilities = str(Path(output_root) / "results.gdb" / "Facilities")
            with self._policy(input_root, output_root):
                result = network_analysis.solve_service_area_once(
                    arcpy,
                    network,
                    facilities,
                    "Driving Time",
                    [5, 10.5],
                    polygons,
                    lines,
                    output_facilities,
                    point_barriers=barriers,
                    time_units="HOURS",
                    distance_units="MILES",
                    travel_direction="TO_FACILITY",
                    geometry_at_cutoff="DISKS",
                    geometry_at_overlap="SPLIT",
                    polygon_detail="HIGH",
                    time_of_day="2026-09-03T09:00:00",
                    time_zone="UTC",
                    ignore_invalid_locations=False,
                )

        solver = arcpy.last_solver
        self.assertTrue(result["solve_succeeded"])
        self.assertEqual(result["cutoffs"], [5.0, 10.5])
        self.assertEqual(result["travel_direction"], "TO_FACILITY")
        self.assertEqual(set(result["outputs"]), {"polygons", "lines", "facilities"})
        self.assertEqual(
            [kind for kind, _path in arcpy.exports],
            ["Polygons", "Lines", "Facilities"],
        )
        self.assertEqual(solver.defaultImpedanceCutoffs, [5.0, 10.5])
        self.assertEqual(solver.timeUnits, "Hours")
        self.assertEqual(solver.distanceUnits, "Miles")
        self.assertEqual(solver.travelDirection, "ToFacility")
        self.assertEqual(solver.geometryAtCutoff, "Disks")
        self.assertEqual(solver.geometryAtOverlap, "Split")
        self.assertEqual(solver.polygonDetail, "High")
        self.assertEqual(solver.outputType, "PolygonsAndLines")
        self.assertEqual(solver.timeZone, "UTC")
        self.assertFalse(solver.ignoreInvalidLocations)
        self.assertEqual(
            solver.loads,
            [
                ("Facilities", os.path.normpath(facilities)),
                ("PointBarriers", os.path.normpath(barriers)),
            ],
        )

    def test_closest_facility_sets_cutoff_units_direction_and_exports(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            network = str(Path(input_root) / "network.gdb" / "Streets_ND")
            incidents = str(Path(input_root) / "incidents.shp")
            facilities = str(Path(input_root) / "facilities.shp")
            routes = str(Path(output_root) / "results.gdb" / "ClosestRoutes")
            output_incidents = str(Path(output_root) / "results.gdb" / "Incidents")
            output_facilities = str(Path(output_root) / "results.gdb" / "Facilities")
            directions = str(Path(output_root) / "results.gdb" / "Directions")
            with self._policy(input_root, output_root):
                result = network_analysis.solve_closest_facility_once(
                    arcpy,
                    network,
                    incidents,
                    facilities,
                    "Driving Time",
                    routes,
                    output_incidents,
                    output_facilities,
                    directions,
                    impedance_cutoff=30,
                    target_facility_count=2,
                    time_units="SECONDS",
                    distance_units="YARDS",
                    travel_direction="FROM_FACILITY",
                    time_of_day="2026-09-03T10:00:00",
                    time_of_day_usage="ARRIVAL_TIME",
                    time_zone="UTC",
                    route_shape_type="STRAIGHT_LINE",
                )

        solver = arcpy.last_solver
        self.assertEqual(result["impedance_cutoff"], 30.0)
        self.assertEqual(result["target_facility_count"], 2)
        self.assertEqual(set(result["outputs"]), {"routes", "incidents", "facilities", "directions"})
        self.assertEqual(solver.defaultImpedanceCutoff, 30.0)
        self.assertEqual(solver.defaultTargetFacilityCount, 2)
        self.assertEqual(solver.timeUnits, "Seconds")
        self.assertEqual(solver.distanceUnits, "Yards")
        self.assertEqual(solver.travelDirection, "FromFacility")
        self.assertEqual(solver.timeOfDayUsage, "ArrivalTime")
        self.assertEqual(solver.routeShapeType, "StraightLine")
        self.assertTrue(solver.returnDirections)
        self.assertEqual(
            solver.loads,
            [
                ("Incidents", os.path.normpath(incidents)),
                ("Facilities", os.path.normpath(facilities)),
            ],
        )

    def test_od_cost_matrix_sets_limits_units_and_exports(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            network = str(Path(input_root) / "network.gdb" / "Streets_ND")
            origins = str(Path(input_root) / "origins.shp")
            destinations = str(Path(input_root) / "destinations.shp")
            lines = str(Path(output_root) / "results.gdb" / "ODLines")
            output_origins = str(Path(output_root) / "results.gdb" / "Origins")
            output_destinations = str(Path(output_root) / "results.gdb" / "Destinations")
            with self._policy(input_root, output_root):
                result = network_analysis.solve_origin_destination_cost_matrix_once(
                    arcpy,
                    network,
                    origins,
                    destinations,
                    "Driving Time",
                    lines,
                    output_origins,
                    output_destinations,
                    impedance_cutoff=45,
                    destination_count=3,
                    time_units="MINUTES",
                    distance_units="METERS",
                    time_zone="UTC",
                    line_shape_type="STRAIGHT_LINE",
                )

        solver = arcpy.last_solver
        self.assertEqual(result["destination_count"], 3)
        self.assertEqual(set(result["outputs"]), {"lines", "origins", "destinations"})
        self.assertEqual([kind for kind, _path in arcpy.exports], ["Lines", "Origins", "Destinations"])
        self.assertEqual(solver.defaultImpedanceCutoff, 45.0)
        self.assertEqual(solver.defaultDestinationCount, 3)
        self.assertEqual(solver.timeUnits, "Minutes")
        self.assertEqual(solver.distanceUnits, "Meters")
        self.assertEqual(solver.lineShapeType, "StraightLine")
        self.assertEqual(
            solver.loads,
            [
                ("Origins", os.path.normpath(origins)),
                ("Destinations", os.path.normpath(destinations)),
            ],
        )

    def test_typed_solver_failure_does_not_export_and_releases_extension(self) -> None:
        arcpy = _Arcpy(solve_succeeded=False)
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "No service area could be generated"):
                    network_analysis.solve_service_area_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "facilities.shp"),
                        "Driving Time",
                        [5],
                        str(Path(output_root) / "results.gdb" / "Polygons"),
                    )

        self.assertEqual(arcpy.exports, [])
        self.assertEqual(arcpy.extension_events[-1], ("in", "Network"))

    def test_typed_solver_rejects_existing_output_without_solver_creation(self) -> None:
        arcpy = _Arcpy()
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            routes = str(Path(output_root) / "results.gdb" / "Routes")
            arcpy.outputs.add(os.path.normpath(routes))
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "拒绝隐式覆盖"):
                    network_analysis.solve_closest_facility_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "incidents.shp"),
                        str(Path(input_root) / "facilities.shp"),
                        "Driving Time",
                        routes,
                    )

        self.assertEqual(arcpy.created_solver_names, [])
        self.assertEqual(arcpy.exports, [])

    def test_portal_url_and_non_network_dataset_are_rejected(self) -> None:
        url_arcpy = _Arcpy()
        with self.assertRaisesRegex(RuntimeError, "Portal/URL"):
            network_analysis.list_travel_modes(
                url_arcpy,
                "https://route.arcgis.com/arcgis/rest/services/World/Route/NAServer",
            )
        with tempfile.TemporaryDirectory() as root, patch.dict(
            os.environ,
            {"ARCGIS_PRO_MCP_INPUT_ROOTS": root},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "actual dataType|实际 dataType"):
                network_analysis.list_travel_modes(
                    _Arcpy(network_data_type="Workspace"),
                    str(Path(root) / "not-a-network.gdb"),
                )
        self.assertEqual(url_arcpy.travel_mode_networks, [])

    def test_typed_solver_enforces_input_and_gp_output_roots(self) -> None:
        with (
            tempfile.TemporaryDirectory() as input_root,
            tempfile.TemporaryDirectory() as output_root,
            tempfile.TemporaryDirectory() as outside_root,
        ):
            facilities = str(Path(input_root) / "facilities.shp")
            polygons = str(Path(output_root) / "results.gdb" / "Polygons")
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "INPUT_ROOTS"):
                    network_analysis.solve_service_area_once(
                        _Arcpy(),
                        str(Path(outside_root) / "network.gdb" / "ND"),
                        facilities,
                        "Driving Time",
                        [5],
                        polygons,
                    )
                with self.assertRaisesRegex(RuntimeError, "GP_OUTPUT_ROOT"):
                    network_analysis.solve_service_area_once(
                        _Arcpy(),
                        str(Path(input_root) / "network.gdb" / "ND"),
                        facilities,
                        "Driving Time",
                        [5],
                        str(Path(outside_root) / "results.gdb" / "Polygons"),
                    )

    def test_invalid_cutoff_unit_and_direction_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            args = (
                str(Path(input_root) / "network.gdb" / "ND"),
                str(Path(input_root) / "facilities.shp"),
                "Driving Time",
                [5],
                str(Path(output_root) / "results.gdb" / "Polygons"),
            )
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, r"cutoffs\[0\]"):
                    network_analysis.solve_service_area_once(_Arcpy(), *args[:3], [0], args[4])
                with self.assertRaisesRegex(RuntimeError, "time_units"):
                    network_analysis.solve_service_area_once(_Arcpy(), *args, time_units="FORTNIGHTS")
                with self.assertRaisesRegex(RuntimeError, "travel_direction"):
                    network_analysis.solve_service_area_once(_Arcpy(), *args, travel_direction="SIDEWAYS")

    def test_export_is_verified_after_solver_reports_success(self) -> None:
        arcpy = _Arcpy(materialize_outputs=False)
        with tempfile.TemporaryDirectory() as input_root, tempfile.TemporaryDirectory() as output_root:
            with self._policy(input_root, output_root):
                with self.assertRaisesRegex(RuntimeError, "地理处理输出不存在"):
                    network_analysis.solve_origin_destination_cost_matrix_once(
                        arcpy,
                        str(Path(input_root) / "network.gdb" / "ND"),
                        str(Path(input_root) / "origins.shp"),
                        str(Path(input_root) / "destinations.shp"),
                        "Driving Time",
                        str(Path(output_root) / "results.gdb" / "Lines"),
                    )


if __name__ == "__main__":
    unittest.main()
