from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import textwrap
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import arcgis_pro_mcp_bootstrap as bootstrap

ROOT = Path(__file__).resolve().parents[1]
PYTHON_ENTRY = ROOT / "接入当前窗口.py"
TOOLBOX_ENTRY = ROOT / "接入当前窗口.pyt"
RESULT_PREFIX = "ARC_GIS_BOOTSTRAP_TEST="


class AttachWindowEntryTests(unittest.TestCase):
    def _run_stale_cache_scenario(self, entry_path: Path, entry_kind: str) -> dict:
        script = textwrap.dedent(
            """
            import importlib
            import importlib.machinery
            import importlib.util
            import json
            import sys
            import types
            from pathlib import Path

            root = Path(sys.argv[1])
            entry_path = Path(sys.argv[2])
            entry_kind = sys.argv[3]
            sys.path.insert(0, str(root))
            sys.modules["arcpy"] = types.ModuleType("arcpy")

            import arcgis_pro_mcp_bootstrap as bootstrap
            from arcgis_pro_mcp import paths, pro_attach, pro_host, server

            old_attach = pro_attach
            old_host = pro_host
            old_paths = paths
            old_server = server
            old_mcp = server.mcp
            assert "arcgis_pro_mcp.__main__" not in sys.modules

            # ArcGIS Pro keeps imported modules alive between toolbox/script runs.
            # Model the previous pro_attach generation which did not define a
            # dependency now imported by pro_host.
            del pro_attach.FORWARDED_ENV_KEYS

            real_reload = importlib.reload
            reload_targets = []
            run_calls = []
            generation_checks = []

            def tracked_reload(module):
                reloaded = real_reload(module)
                reload_targets.append(reloaded.__name__)
                if reloaded.__name__ == "arcgis_pro_mcp_bootstrap":
                    real_load_fresh_host = reloaded._load_fresh_host_unlocked

                    def load_and_observe(repo_root):
                        fresh_host = real_load_fresh_host(repo_root)
                        fresh_attach = sys.modules["arcgis_pro_mcp.pro_attach"]
                        fresh_paths = sys.modules["arcgis_pro_mcp.paths"]
                        fresh_server = sys.modules["arcgis_pro_mcp.server"]
                        generation_checks.append(
                            {
                                "attach_replaced": fresh_attach is not old_attach,
                                "host_replaced": fresh_host is not old_host,
                                "paths_replaced": fresh_paths is not old_paths,
                                "server_replaced": fresh_server is not old_server,
                                "mcp_replaced": fresh_server.mcp is not old_mcp,
                                "constant_restored": hasattr(
                                    fresh_attach,
                                    "FORWARDED_ENV_KEYS",
                                ),
                                "main_entry_absent": (
                                    "arcgis_pro_mcp.__main__" not in sys.modules
                                ),
                            }
                        )
                        fresh_host.main = lambda: run_calls.append(str(repo_root))
                        return fresh_host

                    reloaded._load_fresh_host_unlocked = load_and_observe
                return reloaded

            importlib.reload = tracked_reload

            loader = importlib.machinery.SourceFileLoader(
                f"attach_window_{entry_kind}_under_test",
                str(entry_path),
            )
            spec = importlib.util.spec_from_loader(loader.name, loader)
            entry = importlib.util.module_from_spec(spec)
            loader.exec_module(entry)

            if entry_kind == "toolbox":
                class Messages:
                    def addMessage(self, message):
                        pass

                entry.AttachWindow().execute([], Messages())

            assert reload_targets == ["arcgis_pro_mcp_bootstrap"]
            assert len(run_calls) == 1
            assert generation_checks == [
                {
                    "attach_replaced": True,
                    "host_replaced": True,
                    "paths_replaced": True,
                    "server_replaced": True,
                    "mcp_replaced": True,
                    "constant_restored": True,
                    "main_entry_absent": True,
                }
            ]
            print(
                "ARC_GIS_BOOTSTRAP_TEST="
                + json.dumps(
                    {
                        "reload_targets": reload_targets,
                        "run_calls": len(run_calls),
                        "generation": generation_checks[0],
                    },
                    sort_keys=True,
                )
            )
            """
        )

        env = os.environ.copy()
        # Mirror Windows CI consoles (cp1252) so Chinese status lines stay crash-free.
        env["PYTHONIOENCODING"] = "cp1252"
        completed = subprocess.run(
            [sys.executable, "-c", script, str(ROOT), str(entry_path), entry_kind],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        result_line = next(
            (line for line in completed.stdout.splitlines() if line.startswith(RESULT_PREFIX)),
            None,
        )
        self.assertIsNotNone(
            result_line,
            msg=f"missing result marker in stdout:\n{completed.stdout}",
        )
        return json.loads(result_line.removeprefix(RESULT_PREFIX))

    def _run_wrong_bootstrap_source_scenario(
        self,
        entry_path: Path,
        entry_kind: str,
    ) -> dict:
        script = textwrap.dedent(
            """
            import importlib
            import importlib.machinery
            import importlib.util
            import json
            import sys
            import types
            from pathlib import Path

            root = Path(sys.argv[1])
            entry_path = Path(sys.argv[2])
            entry_kind = sys.argv[3]
            sys.path.insert(0, str(root))
            sys.modules["arcpy"] = types.ModuleType("arcpy")

            wrong_bootstrap = types.ModuleType("arcgis_pro_mcp_bootstrap")
            wrong_bootstrap.__file__ = str(
                root.parent / "wrong-checkout" / "arcgis_pro_mcp_bootstrap.py"
            )
            run_calls = []
            reload_calls = []
            wrong_bootstrap.run_host = lambda *args, **kwargs: run_calls.append(args)
            sys.modules["arcgis_pro_mcp_bootstrap"] = wrong_bootstrap

            def retain_wrong_bootstrap(module):
                assert module is wrong_bootstrap
                reload_calls.append(module.__name__)
                return module

            importlib.reload = retain_wrong_bootstrap

            loader = importlib.machinery.SourceFileLoader(
                f"attach_window_wrong_source_{entry_kind}_under_test",
                str(entry_path),
            )
            spec = importlib.util.spec_from_loader(loader.name, loader)
            entry = importlib.util.module_from_spec(spec)

            error = None
            try:
                loader.exec_module(entry)
                if entry_kind == "toolbox":
                    class Messages:
                        def addMessage(self, message):
                            pass

                    entry.AttachWindow().execute([], Messages())
            except RuntimeError as exc:
                error = str(exc)

            expected = str((root / "arcgis_pro_mcp_bootstrap.py").resolve())
            assert error is not None
            assert "启动器未从当前仓库加载" in error
            assert expected in error
            assert run_calls == []
            assert reload_calls == ["arcgis_pro_mcp_bootstrap"]
            print(
                "ARC_GIS_BOOTSTRAP_TEST="
                + json.dumps(
                    {
                        "error": error,
                        "reload_calls": len(reload_calls),
                        "run_calls": len(run_calls),
                    },
                    sort_keys=True,
                )
            )
            """
        )

        env = os.environ.copy()
        # Mirror Windows CI consoles (cp1252) so Chinese status lines stay crash-free.
        env["PYTHONIOENCODING"] = "cp1252"
        completed = subprocess.run(
            [sys.executable, "-c", script, str(ROOT), str(entry_path), entry_kind],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
        )
        self.assertEqual(
            completed.returncode,
            0,
            msg=f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )
        result_line = next(
            (line for line in completed.stdout.splitlines() if line.startswith(RESULT_PREFIX)),
            None,
        )
        self.assertIsNotNone(
            result_line,
            msg=f"missing result marker in stdout:\n{completed.stdout}",
        )
        return json.loads(result_line.removeprefix(RESULT_PREFIX))

    def _assert_fresh_generation(self, result: dict) -> None:
        self.assertEqual(result["reload_targets"], ["arcgis_pro_mcp_bootstrap"])
        self.assertEqual(result["run_calls"], 1)
        self.assertEqual(
            result["generation"],
            {
                "attach_replaced": True,
                "constant_restored": True,
                "host_replaced": True,
                "main_entry_absent": True,
                "mcp_replaced": True,
                "paths_replaced": True,
                "server_replaced": True,
            },
        )

    def test_toolbox_entry_replaces_stale_package_generation(self) -> None:
        self._assert_fresh_generation(
            self._run_stale_cache_scenario(TOOLBOX_ENTRY, "toolbox")
        )

    def test_python_entry_replaces_stale_package_generation(self) -> None:
        self._assert_fresh_generation(
            self._run_stale_cache_scenario(PYTHON_ENTRY, "script")
        )

    def test_toolbox_entry_rejects_bootstrap_from_wrong_checkout(self) -> None:
        result = self._run_wrong_bootstrap_source_scenario(TOOLBOX_ENTRY, "toolbox")
        self.assertEqual(result["reload_calls"], 1)
        self.assertEqual(result["run_calls"], 0)

    def test_python_entry_rejects_bootstrap_from_wrong_checkout(self) -> None:
        result = self._run_wrong_bootstrap_source_scenario(PYTHON_ENTRY, "script")
        self.assertEqual(result["reload_calls"], 1)
        self.assertEqual(result["run_calls"], 0)


class BootstrapSafetyTests(unittest.TestCase):
    def test_incompatible_fastmcp_has_actionable_version_error(self) -> None:
        with (
            patch.object(
                bootstrap.importlib,
                "import_module",
                side_effect=ModuleNotFoundError("mcp.server.fastmcp"),
            ),
            patch.object(
                bootstrap.importlib.metadata,
                "version",
                return_value="2.1.1",
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, r"mcp>=1\.20,<2"):
                bootstrap._require_compatible_fastmcp()

    def test_compatible_fastmcp_returns_distribution_version(self) -> None:
        compatible = types.SimpleNamespace(FastMCP=object)
        with (
            patch.object(
                bootstrap.importlib,
                "import_module",
                return_value=compatible,
            ),
            patch.object(
                bootstrap.importlib.metadata,
                "version",
                return_value="1.21.0",
            ),
        ):
            self.assertEqual(bootstrap._require_compatible_fastmcp(), "1.21.0")

    def test_active_host_marker_rejects_refresh_before_cache_purge(self) -> None:
        with (
            patch.dict(os.environ, {"ARCGIS_PRO_MCP_IN_PRO_HOST": "1"}, clear=False),
            patch.object(bootstrap, "_remove_package_modules") as remove_modules,
        ):
            with self.assertRaisesRegex(RuntimeError, "仍处于窗口宿主模式"):
                bootstrap._load_fresh_host_unlocked(ROOT)
        remove_modules.assert_not_called()

    def test_launch_lock_survives_bootstrap_reload(self) -> None:
        first_lock = bootstrap._process_lock()
        reloaded = importlib.reload(bootstrap)
        self.assertIs(reloaded._process_lock(), first_lock)

        self.assertTrue(first_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(RuntimeError, "已经在启动或运行"):
                with reloaded._exclusive_launch():
                    self.fail("a second bootstrap launch must not enter the lock")
        finally:
            first_lock.release()

    def test_failed_fresh_import_restores_modules_and_sys_path_exactly(self) -> None:
        importlib.import_module("arcgis_pro_mcp")
        importlib.import_module("arcgis_pro_mcp.pro_attach")
        importlib.import_module("arcgis_pro_mcp.pro_host")

        package_prefix = "arcgis_pro_mcp."
        old_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "arcgis_pro_mcp" or name.startswith(package_prefix)
        }
        original_path = list(sys.path)
        simulated_user_site = str(ROOT / ".simulated-pro-user-site")

        def controlled_import(name: str) -> types.ModuleType:
            if name == "arcgis_pro_mcp.pro_host":
                sys.path.append(simulated_user_site)
                raise ImportError("controlled fresh pro_host import failure")
            module = types.ModuleType(name)
            module.__file__ = str(ROOT.joinpath(*name.split("."))) + ".py"
            sys.modules[name] = module
            return module

        try:
            with (
                patch.dict(
                    os.environ,
                    {"ARCGIS_PRO_MCP_IN_PRO_HOST": ""},
                    clear=False,
                ),
                patch.object(
                    bootstrap.importlib,
                    "import_module",
                    side_effect=controlled_import,
                ),
            ):
                with self.assertRaisesRegex(
                    ImportError,
                    "controlled fresh pro_host import failure",
                ):
                    bootstrap._load_fresh_host_unlocked(ROOT)

            self.assertEqual(sys.path, original_path)
            restored_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "arcgis_pro_mcp" or name.startswith(package_prefix)
            }
            self.assertEqual(restored_modules.keys(), old_modules.keys())
            for name, old_module in old_modules.items():
                self.assertIs(restored_modules[name], old_module)
        finally:
            sys.path[:] = original_path
            for name in list(sys.modules):
                if name == "arcgis_pro_mcp" or name.startswith(package_prefix):
                    sys.modules.pop(name, None)
            sys.modules.update(old_modules)

    def test_invalidate_caches_failure_restores_none_modules_and_sys_path(self) -> None:
        importlib.import_module("arcgis_pro_mcp")
        importlib.import_module("arcgis_pro_mcp.pro_attach")
        importlib.import_module("arcgis_pro_mcp.pro_host")

        package_prefix = "arcgis_pro_mcp."
        before_test_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "arcgis_pro_mcp" or name.startswith(package_prefix)
        }
        none_name = "arcgis_pro_mcp.cached_import_failure"
        transient_name = "arcgis_pro_mcp.transient_import_failure"
        sys.modules[none_name] = None
        expected_modules = {
            name: module
            for name, module in sys.modules.items()
            if name == "arcgis_pro_mcp" or name.startswith(package_prefix)
        }
        original_path = list(sys.path)
        simulated_import_path = str(ROOT / ".simulated-invalidate-path")

        def fail_after_mutation() -> None:
            sys.path.append(simulated_import_path)
            transient = types.ModuleType(transient_name)
            transient.__file__ = str(ROOT / "arcgis_pro_mcp" / "transient.py")
            sys.modules[transient_name] = transient
            raise RuntimeError("controlled invalidate_caches failure")

        try:
            with (
                patch.dict(
                    os.environ,
                    {"ARCGIS_PRO_MCP_IN_PRO_HOST": ""},
                    clear=False,
                ),
                patch.object(
                    bootstrap.importlib,
                    "invalidate_caches",
                    side_effect=fail_after_mutation,
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "controlled invalidate_caches failure",
                ):
                    bootstrap._load_fresh_host_unlocked(ROOT)

            self.assertEqual(sys.path, original_path)
            restored_modules = {
                name: module
                for name, module in sys.modules.items()
                if name == "arcgis_pro_mcp" or name.startswith(package_prefix)
            }
            self.assertEqual(restored_modules.keys(), expected_modules.keys())
            self.assertIn(none_name, restored_modules)
            self.assertIsNone(restored_modules[none_name])
            self.assertNotIn(transient_name, restored_modules)
            for name, old_module in expected_modules.items():
                self.assertIs(restored_modules[name], old_module)
        finally:
            sys.path[:] = original_path
            for name in list(sys.modules):
                if name == "arcgis_pro_mcp" or name.startswith(package_prefix):
                    sys.modules.pop(name, None)
            sys.modules.update(before_test_modules)

    def test_repo_generation_rejects_module_without_source(self) -> None:
        missing_source_name = "arcgis_pro_mcp.missing_source_under_test"
        sys.modules[missing_source_name] = types.ModuleType(missing_source_name)
        try:
            with self.assertRaisesRegex(RuntimeError, "没有可验证的 __file__ 来源"):
                bootstrap._assert_repo_generation(ROOT)
        finally:
            sys.modules.pop(missing_source_name, None)


if __name__ == "__main__":
    unittest.main()
