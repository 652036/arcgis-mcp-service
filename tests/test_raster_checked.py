from __future__ import annotations

import copy
import os
import tempfile
import unittest
import uuid
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import analysis_quality as qa
from arcgis_pro_mcp import raster_checked as checked


class CheckedRasterContractTests(unittest.TestCase):
    """Policy and evidence checks that run in ordinary CI without GDAL or ArcPy."""

    def setUp(self) -> None:
        self.contexts = ExitStack()
        self.addCleanup(self.contexts.close)
        temporary = self.contexts.enter_context(tempfile.TemporaryDirectory())
        self.root = Path(temporary).resolve()
        self.contexts.enter_context(patch.dict(os.environ, {
            "ARCGIS_PRO_MCP_ALLOW_WRITE": "1",
            "ARCGIS_PRO_MCP_INPUT_ROOTS": str(self.root),
            "ARCGIS_PRO_MCP_GP_OUTPUT_ROOT": str(self.root),
        }, clear=True))
        self.backend = self.contexts.enter_context(patch.object(
            checked, "_backend", side_effect=AssertionError("Contract tests must not load GDAL")
        ))
        self.run_id = "12345678-1234-4567-8abc-123456789abc"
        self.source = self.root / "source.tif"
        self.source.write_bytes(b"synthetic bytes for version checks only")
        self.output = self.root / "output.tif"
        self.output.write_bytes(b"synthetic output version")

    def asset(self) -> dict:
        fingerprint, files = checked._file_version(self.source)
        return {
            "asset_id": "sha256:" + fingerprint, "fingerprint": fingerprint,
            "path": str(self.source), "role": "CONTINUOUS",
            "identity_basis": "synthetic fixture", "vector_layer": "",
            "metadata": {"client_claim": "not authoritative"}, "files": files,
        }

    def report(self) -> dict:
        return {
            "run_id": self.run_id, "execution_status": "SUCCEEDED",
            "checks": [qa.check(name, True) for name in (
                "grid", "coverage", "outside_aoi", "values_preserved", "input_versions", "environment_restored"
            )],
            "assets": {"source": self.asset()},
            "output_raster": str(self.output),
            "output_fingerprint": checked._file_version(self.output)[0],
        }

    def status(self, report: dict) -> dict:
        with patch.object(checked.private_state, "read_private_json", return_value=copy.deepcopy(report)):
            return checked.result_status(self.run_id)

    def request(self, **changes) -> dict:
        return dict({
            "arcpy": MagicMock(), "run_id": self.run_id,
            "source_asset": self.asset(), "grid_asset": dict(self.asset(), role="GRID_REFERENCE"),
            "clip_mode": "RECTANGLE", "minimum_coverage": 1,
            "rectangle": [0, 0, 10, 10], "rectangle_crs": "explicit CRS fixture",
        }, **changes)

    def test_invalid_run_ids_have_consistent_errors_before_io(self) -> None:
        values = ["not-a-uuid", "", None, 123, uuid.UUID(self.run_id),
                  self.run_id.upper(), self.run_id.replace("-", ""), "../" + self.run_id]
        with patch.object(checked.private_state, "read_private_json") as read:
            for value in values:
                for operation in (checked._run_dir, checked.result_status):
                    with self.subTest(value=value, operation=operation.__name__):
                        with self.assertRaisesRegex(RuntimeError, "INVALID_RUN_ID"):
                            operation(value)
            read.assert_not_called()

    def test_valid_run_directory_is_not_created_by_lookup(self) -> None:
        folder = checked._run_dir(self.run_id)
        self.assertEqual(folder, self.root / "_analysis_runs" / self.run_id)
        self.assertFalse(folder.exists())
        with patch.object(checked.private_state, "read_private_json", return_value={}):
            with self.assertRaisesRegex(RuntimeError, "RUN_NOT_FOUND_OR_UNTRUSTED"):
                checked.result_status(self.run_id)
        self.assertFalse(folder.exists())

    def test_missing_asset_fields_are_rejected_before_inspection(self) -> None:
        for key in ("path", "identity_basis", "asset_id", "fingerprint", "role"):
            asset = self.asset()
            del asset[key]
            with self.subTest(key=key), patch.object(checked, "asset_info") as inspect:
                with self.assertRaisesRegex(RuntimeError, "INVALID_ASSET_CONTRACT.*" + key):
                    checked._verify_asset(asset, {"CONTINUOUS"})
                inspect.assert_not_called()

    def test_invalid_asset_shapes_types_and_roles_are_rejected(self) -> None:
        for asset in (None, [], dict(self.asset(), unexpected=True), dict(self.asset(), path=3),
                      dict(self.asset(), identity_basis=" "), dict(self.asset(), vector_layer=[])):
            with self.subTest(asset=asset), self.assertRaisesRegex(RuntimeError, "INVALID_ASSET_CONTRACT"):
                checked._verify_asset(asset, {"CONTINUOUS"})
        with self.assertRaisesRegex(RuntimeError, "ASSET_ROLE_MISMATCH"):
            checked._verify_asset(self.asset(), {"GRID_REFERENCE"})

    def test_asset_metadata_is_reloaded_and_upstream_eligibility_checked(self) -> None:
        asset = self.asset()
        fresh = dict(asset, metadata={"server_fact": "fresh inspection"})
        with patch.object(checked, "asset_info", return_value=fresh) as inspect, patch.object(
            checked, "_require_known_output_eligible"
        ) as eligible:
            result = checked._verify_asset(asset, {"CONTINUOUS"})
        self.assertIs(result, fresh)
        inspect.assert_called_once_with(str(self.source), "CONTINUOUS", "synthetic fixture", "")
        eligible.assert_called_once_with(fresh)

    def test_changed_asset_version_cannot_reuse_registration(self) -> None:
        asset = self.asset()
        for field in ("asset_id", "fingerprint"):
            with self.subTest(field=field), patch.object(
                checked, "asset_info", return_value=dict(asset, **{field: "changed"})
            ), patch.object(checked, "_require_known_output_eligible") as eligible:
                with self.assertRaisesRegex(RuntimeError, "ASSET_VERSION_MISMATCH"):
                    checked._verify_asset(asset, {"CONTINUOUS"})
                eligible.assert_not_called()

    def test_geometry_contract_rejected_before_creating_run(self) -> None:
        cases = [
            {"clip_mode": "AUTO"},
            {"clip_mode": "POLYGON_MASK", "boundary_asset": self.asset()},
            {"clip_mode": "POLYGON_MASK", "rectangle": None, "rectangle_crs": ""},
            {"boundary_asset": self.asset()}, {"rectangle": None}, {"rectangle_crs": ""},
            {"rectangle": [0, 0, 0, 10]}, {"rectangle": [0, 0, 10]},
            {"rectangle": [0, 0, float("nan"), 10]},
        ]
        for changes in cases:
            with self.subTest(changes=changes), patch.object(checked, "_run_dir") as run_dir:
                with self.assertRaises(RuntimeError):
                    checked.run_clip_checked(**self.request(**changes))
                run_dir.assert_not_called()

    def test_coverage_and_resource_policies_rejected_before_creating_run(self) -> None:
        cases = [{"minimum_coverage": x} for x in (-1, 2, float("inf"), True, 0.9)]
        cases += [{"maximum_output_cells": x} for x in (0, -1, 1.5, True)]
        for changes in cases:
            with self.subTest(changes=changes), patch.object(checked, "_run_dir") as run_dir:
                with self.assertRaises(RuntimeError):
                    checked.run_clip_checked(**self.request(**changes))
                run_dir.assert_not_called()

    def test_validity_contract_rejected_before_creating_run(self) -> None:
        cases = [{}, [], False, {"value_space": "UNKNOWN"}, {"value_space": "RAW", "extra": 1},
                 {"value_space": "RAW", "invalid_values": "0"},
                 {"value_space": "RAW", "invalid_values": [float("nan")]},
                 {"value_space": "RAW", "minimum": True},
                 {"value_space": "RAW", "minimum": 2, "maximum": 1}]
        for validity in cases:
            with self.subTest(validity=validity), patch.object(checked, "_run_dir") as run_dir:
                with self.assertRaises(RuntimeError):
                    checked.run_clip_checked(**self.request(validity=validity))
                run_dir.assert_not_called()

    def test_reused_run_id_returns_status_without_reexecuting(self) -> None:
        folder = self.root / "existing-run"
        folder.mkdir()
        (folder / "report.json").touch()
        request = self.request(minimum_coverage=0.9, missing_data_reason="declared gap",
                               validity={"value_space": "PHYSICAL", "invalid_values": [0], "minimum": 1})
        plan = {k: v for k, v in request.items() if k not in {"arcpy", "run_id"}}
        plan.update(boundary_asset=None, maximum_output_cells=100_000_000)
        with patch.object(checked, "_run_dir", return_value=folder), patch.object(
            checked.private_state, "read_private_json", return_value={"plan_digest": checked._digest(plan)}
        ), patch.object(checked, "result_status", return_value={"existing": True}) as status, patch.object(
            checked, "_verify_asset"
        ) as verify:
            self.assertEqual(checked.run_clip_checked(**request), {"existing": True})
            status.assert_called_once_with(self.run_id)
            verify.assert_not_called()
        request["arcpy"].management.Clip.assert_not_called()

    def test_run_id_cannot_be_reused_for_a_different_plan(self) -> None:
        folder = self.root / "existing-run"
        folder.mkdir()
        (folder / "report.json").touch()
        with patch.object(checked, "_run_dir", return_value=folder), patch.object(
            checked.private_state, "read_private_json", return_value={"plan_digest": "other-plan"}
        ):
            with self.assertRaisesRegex(RuntimeError, "RUN_ID_PLAN_CONFLICT"):
                checked.run_clip_checked(**self.request())

    def test_status_recomputes_eligibility_instead_of_trusting_report_boolean(self) -> None:
        report = self.report()
        report.update(eligible_for_downstream=False, qa_status="FAILED")
        self.assertTrue(self.status(report)["eligible_for_downstream"])
        for changes in ({"checks": []}, {"output_fingerprint": None}, {"execution_status": "FAILED"}):
            forged = dict(report, eligible_for_downstream=True, qa_status="PASSED", **changes)
            with self.subTest(changes=changes):
                self.assertFalse(self.status(forged)["eligible_for_downstream"])

    def test_running_report_is_unknown_and_not_eligible(self) -> None:
        status = self.status(dict(self.report(), execution_status="RUNNING"))
        self.assertEqual(status["execution_status"], "UNKNOWN")
        self.assertFalse(status["eligible_for_downstream"])

    def test_modified_input_or_output_invalidates_eligibility(self) -> None:
        for path in (self.source, self.output):
            report = self.report()
            path.write_bytes(path.read_bytes() + b"modified")
            with self.subTest(path=path):
                self.assertFalse(self.status(report)["eligible_for_downstream"])

    def test_malformed_reports_fail_closed_with_useful_errors(self) -> None:
        for changes in ({"run_id": str(uuid.uuid4())}, {"checks": None},
                        {"checks": [{}]}, {"checks": ["PASSED"]}):
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, "INVALID_RUN_REPORT"):
                self.status(dict(self.report(), **changes))
        report = self.report()
        report["assets"] = {"source": {}}
        self.assertFalse(self.status(report)["eligible_for_downstream"])

    def test_window_uses_entire_requested_domain_not_reference_intersection(self) -> None:
        grid = dict(x0=0, y0=10, dx=1, dy=1, cols=4, rows=4, crs_wkt="fixture")
        expected = dict(grid, x0=-1, y0=11, cols=12, rows=12)
        self.assertEqual(checked._window(grid, [-1, -1, 11, 11]), expected)
        self.assertEqual(checked._window(grid, [0.25, 0.25, 9.75, 9.75]), dict(grid, cols=10, rows=10))
        self.assertEqual(checked._window(grid, [0, 0, 10, 10]), dict(grid, cols=10, rows=10))

    def test_sidecar_changes_alter_version_but_primary_rename_does_not(self) -> None:
        before = checked._file_version(self.source)[0]
        renamed = self.root / "renamed.tif"
        renamed.write_bytes(self.source.read_bytes())
        self.assertEqual(checked._file_version(renamed)[0], before)
        Path(str(renamed) + ".aux.xml").write_text("metadata", encoding="utf-8")
        self.assertNotEqual(checked._file_version(renamed)[0], before)


if __name__ == "__main__":
    unittest.main()
