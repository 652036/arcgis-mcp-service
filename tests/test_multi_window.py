"""Authenticated two-window routing over real loopback HTTP, without ArcPy."""
from __future__ import annotations

import json
import os
import secrets
import socket
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from mcp.server.fastmcp import FastMCP

from arcgis_pro_mcp import paths, pro_attach, pro_host, server
from arcgis_pro_mcp.private_state import write_private_json
from arcgis_pro_mcp.tool_protocol import tool_policy


class MultiWindowTests(unittest.TestCase):
    def setUp(self):
        pro_attach.clear_confirmed_target()
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.env_patch = patch.dict(os.environ, {pro_attach.ENV_IN_HOST: "", pro_attach.ENV_PORT: ""})
        self.directory_patch = patch.object(pro_attach, "default_state_directory", return_value=self.root)
        self.env_patch.start()
        self.directory_patch.start()
        self.hosts = []

    def tearDown(self):
        for host in self.hosts:
            if not host.stopped:
                host.httpd.shutdown()
                host.httpd.server_close()
                host.thread.join(timeout=2)
        pro_attach.clear_confirmed_target()
        self.directory_patch.stop()
        self.env_patch.stop()
        self.directory.cleanup()

    def host(self, pid, project, *, ready=True):
        state = {
            "service": pro_attach.SERVICE_NAME, "protocol_version": pro_attach.PROTOCOL_VERSION,
            "package_version": pro_attach.PACKAGE_VERSION, "pid": pid,
            "session_id": secrets.token_hex(16), "token": secrets.token_urlsafe(32),
            "project": project, "ready": ready, "ok": True, "busy": False, "state": "READY",
        }
        calls = []

        def stop_mock_host(target_host):
            if not target_host.stopped:
                target_host.stopped = True
                target_host.httpd.shutdown()
                target_host.httpd.server_close()
                target_host.thread.join(timeout=2)
                target_host.path.unlink(missing_ok=True)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def send(self, status, value):
                raw = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.headers.get(pro_attach.TOKEN_HEADER) != state["token"]:
                    return self.send(403, {"error": "denied"})
                self.send(200, {k: v for k, v in state.items() if k != "token"})

            def do_POST(self):
                if self.headers.get(pro_attach.TOKEN_HEADER) != state["token"]:
                    return self.send(403, {"error": "denied"})
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.split("?", 1)[0] == "/stop":
                    state["ready"] = False
                    state["state"] = "STOPPING"
                    self.send(200, {"ok": True, "stopping": True, "session_id": state["session_id"]})
                    threading.Thread(target=stop_mock_host, args=(host_ref[0],), daemon=True).start()
                    return
                if payload.get("session_id") != state["session_id"] or payload.get("expected_project") != state["project"]:
                    return self.send(409, {"error": "stale target"})
                if payload["arguments"]["aprx_path"] != "CURRENT":
                    return self.send(400, {"error": "host must receive normalized CURRENT"})
                calls.append(payload)
                self.send(200, {
                    "ok": True, "session_id": state["session_id"], "protocol_version": pro_attach.PROTOCOL_VERSION,
                    "request_id": payload["request_id"], "result": {"project": state["project"], "pid": pid},
                })

        host_ref = []
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        httpd.daemon_threads = True
        state["port"] = httpd.server_address[1]
        thread = threading.Thread(
            target=httpd.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        thread.start()
        private_path = self.root / f"arcgis-pro-mcp-instance-{pid}-{state['session_id']}.json"
        write_private_json(private_path, state, temp_tag=state["session_id"])
        host = SimpleNamespace(state=state, calls=calls, httpd=httpd, thread=thread, stopped=False, path=private_path)
        host_ref.append(host)
        self.hosts.append(host)
        return host

    def two_hosts(self):
        return self.host(101, r"C:\GIS\a.aprx"), self.host(202, r"C:\GIS\b.aprx")

    def proxy(self):
        mcp = FastMCP("multi-window-test")

        @mcp.tool(name="probe")
        def probe(aprx_path: str):
            raise AssertionError("CURRENT must never fall back to local file mode")

        pro_attach.install_stdio_proxy(mcp)
        return mcp._tool_manager.get_tool("probe").fn

    def test_discovery_returns_two_windows_without_credentials_and_deduplicates_legacy(self):
        a, _ = self.two_hosts()
        write_private_json(self.root / f"arcgis-pro-mcp-host-{a.state['port']}.json", a.state, temp_tag="duplicate")
        result = pro_attach.list_windows()
        self.assertEqual(result["window_count"], 2)
        self.assertEqual({w["process_id"] for w in result["windows"]}, {101, 202})
        serialized = json.dumps(result)
        for host in self.hosts:
            self.assertNotIn(host.state["token"], serialized)
        self.assertNotIn("_endpoint_token", serialized)

    def test_ambiguous_current_requires_selection(self):
        self.two_hosts()
        with self.assertRaisesRegex(RuntimeError, "发现多个"):
            pro_attach.host_health()

    def test_select_default_and_switch_without_port_environment(self):
        a, b = self.two_hosts()
        first = pro_attach.select_window(process_id=101, expected_project=a.state["project"])
        self.assertTrue(first["target_confirmed"])
        self.assertEqual(pro_attach.host_health()["pid"], 101)
        pro_attach.select_window(window_id=f"202:{b.state['session_id']}")
        self.assertEqual(json.loads(self.proxy()(aprx_path="CURRENT"))["pid"], 202)
        self.assertEqual(len(a.calls), 0)
        self.assertEqual(len(b.calls), 1)

    def test_explicit_pid_is_independent_of_default_and_parallel_calls_are_isolated(self):
        a, b = self.two_hosts()
        pro_attach.select_window(process_id=101)
        pro_attach.host_status(process_id=202, confirm_target=True)
        self.assertEqual(pro_attach.host_health()["pid"], 101)
        proxy = self.proxy()
        pids = [101, 202] * 12
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda pid: json.loads(proxy(aprx_path=f"CURRENT:{pid}")), pids))
        self.assertEqual([r["pid"] for r in results], pids)
        self.assertEqual(len(a.calls), 12)
        self.assertEqual(len(b.calls), 12)
        self.assertTrue(all(p["expected_project"] == a.state["project"] for p in a.calls))
        self.assertTrue(all(p["expected_project"] == b.state["project"] for p in b.calls))
        self.assertIsNone(pro_attach._WINDOW_SCOPE.get())

    def test_explicit_call_requires_its_own_confirmation(self):
        self.two_hosts()
        pro_attach.select_window(process_id=101)
        with self.assertRaisesRegex(RuntimeError, "确认|变化"):
            self.proxy()(aprx_path="CURRENT:202")

    def test_project_switch_rejects_call_until_reconfirmed(self):
        a, _ = self.two_hosts()
        pro_attach.select_window(process_id=101)
        a.state["project"] = r"C:\GIS\changed.aprx"
        with self.assertRaisesRegex(RuntimeError, "会话或工程已变化"):
            self.proxy()(aprx_path="CURRENT:101")
        self.assertEqual(a.calls, [])
        pro_attach.host_status(process_id=101, confirm_target=True)
        self.assertEqual(json.loads(self.proxy()(aprx_path="CURRENT:101"))["project"], a.state["project"])

    def test_closed_selected_window_never_switches_to_remaining_window(self):
        a, b = self.two_hosts()
        pro_attach.select_window(process_id=101)
        a.httpd.shutdown()
        a.httpd.server_close()
        a.thread.join(timeout=2)
        a.stopped = True
        with self.assertRaisesRegex(RuntimeError, "不会自动改连"):
            self.proxy()(aprx_path="CURRENT")
        self.assertEqual(pro_attach.list_windows()["window_count"], 1)
        self.assertEqual(b.calls, [])

    def test_snapshot_call_remains_on_original_window_after_selection_changes(self):
        a, _ = self.two_hosts()
        pro_attach.select_window(process_id=101)
        snapshot = pro_attach.host_health()
        pro_attach.select_window(process_id=202)
        result = json.loads(pro_attach.host_call("probe", {"aprx_path": "CURRENT"}, snapshot))
        self.assertEqual(result["pid"], 101)
        self.assertEqual(len(a.calls), 1)

    def test_mismatched_expected_project_does_not_change_default(self):
        self.two_hosts()
        pro_attach.select_window(process_id=101)
        with self.assertRaisesRegex(RuntimeError, "未唯一匹配"):
            pro_attach.select_window(process_id=202, expected_project=r"C:\GIS\wrong.aprx")
        self.assertEqual(pro_attach.host_health()["pid"], 101)

    def test_restart_requires_new_confirmation_and_does_not_reuse_old_session(self):
        a, _ = self.two_hosts()
        pro_attach.host_status(process_id=101, confirm_target=True)
        a.state["session_id"] = secrets.token_hex(16)
        with self.assertRaisesRegex(RuntimeError, "未连接|不唯一"):
            self.proxy()(aprx_path="CURRENT:101")
        self.assertEqual(a.calls, [])

    def test_unrelated_and_wrong_filename_identity_records_are_not_probed(self):
        a = self.host(101, r"C:\GIS\a.aprx")
        wrong = {**a.state, "pid": 202, "port": 1}
        write_private_json(self.root / "arcgis-pro-mcp-instance-303-other.json", wrong, temp_tag="wrong")
        (self.root / "unrelated.json").write_text(json.dumps(wrong))
        with patch.object(pro_attach, "_probe_endpoint", wraps=pro_attach._probe_endpoint) as probe:
            result = pro_attach.list_windows()
        self.assertEqual(result["window_count"], 1)
        self.assertEqual(probe.call_count, 1)

    def test_same_project_in_two_processes_requires_pid_or_window_id(self):
        self.host(101, r"C:\GIS\shared.aprx")
        self.host(202, r"C:\GIS\shared.aprx")
        with self.assertRaisesRegex(RuntimeError, "2 个"):
            pro_attach.select_window(expected_project=r"C:\GIS\shared.aprx")

    def test_current_selector_validation_and_tool_contracts(self):
        self.assertTrue(paths.is_current_project_token("CURRENT:123"))
        self.assertTrue(pro_attach.should_forward_to_host({"aprx_path": "CURRENT:123"}))
        for invalid in ["CURRENT:0", "CURRENT:-2", "CURRENT:abc", "CURRENT:１２３", "CURRENT:999999999999"]:
            with self.assertRaises(RuntimeError):
                paths.is_current_project_token(invalid)
        selection = tool_policy("arcgis_pro_select_window")
        self.assertFalse(selection["read_only"])
        self.assertFalse(selection["destructive"])
        self.assertEqual(selection["gates"], [])
        listing = json.loads(server.arcgis_pro_list_windows())
        self.assertEqual(listing["window_count"], 0)
        with patch.object(server, "_arcpy") as arcpy:
            with self.assertRaisesRegex(RuntimeError, "必须通过 MCP 代理路由"):
                server._open_project(f"CURRENT:{os.getpid()+1}")
            arcpy.assert_not_called()

    def test_qualified_job_submission_targets_its_window(self):
        a, b = self.two_hosts()
        pro_attach.select_window(process_id=101)
        pro_attach.host_status(process_id=202, confirm_target=True)
        result = pro_attach.submit_host_job("probe", {"aprx_path": "CURRENT:202"})
        self.assertEqual(result["result"]["pid"], 202)
        self.assertEqual(a.calls, [])
        self.assertEqual(len(b.calls), 1)

    def test_job_status_cancel_and_events_have_per_request_scope(self):
        with patch.object(pro_attach, "host_job_status", side_effect=lambda _: {"scope": pro_attach._WINDOW_SCOPE.get()}) as status:
            result = json.loads(server.arcgis_pro_window_job_status("job_a", process_id=101))
        self.assertEqual(result["scope"], [101, ""])
        status.assert_called_once_with("job_a")
        with patch.object(pro_attach, "cancel_host_job", side_effect=lambda _: {"scope": pro_attach._WINDOW_SCOPE.get()}):
            result = json.loads(server.arcgis_pro_window_job_cancel("job_b", process_id=202))
        self.assertEqual(result["scope"], [202, ""])
        with patch.object(pro_attach, "wait_for_window_change", side_effect=lambda *a: {"scope": pro_attach._WINDOW_SCOPE.get()}):
            result = json.loads(server.arcgis_pro_window_wait_for_change(process_id=101))
        self.assertEqual(result["scope"], [101, ""])
        self.assertIsNone(pro_attach._WINDOW_SCOPE.get())

    def test_status_confirms_explicit_window_without_changing_default(self):
        self.two_hosts()
        pro_attach.select_window(process_id=101)
        result = json.loads(server.arcgis_pro_window_status(process_id=202))
        self.assertEqual(result["host_pid"], 202)
        self.assertTrue(result["target_confirmed"])
        self.assertEqual(pro_attach.host_health()["pid"], 101)

    def test_host_snapshot_cannot_redirect_to_an_external_address(self):
        with self.assertRaisesRegex(RuntimeError, "端点无效"):
            pro_attach._snapshot_endpoint({"_endpoint_base": "https://example.invalid:123", "_endpoint_token": "secret"})

    def test_unconfirmed_window_snapshot_cannot_be_used_for_another_pid(self):
        self.two_hosts()
        pro_attach.select_window(process_id=101)
        snapshot = pro_attach.host_health()
        with self.assertRaisesRegex(RuntimeError, "进程号.*不一致"):
            pro_attach.host_call("probe", {"aprx_path": "CURRENT:202"}, snapshot)
        with self.assertRaisesRegex(RuntimeError, "进程号.*不一致"):
            pro_attach.submit_host_job("probe", {"aprx_path": "CURRENT:202"}, host_snapshot=snapshot)

    def test_own_state_cleanup_is_independent_for_two_hosts(self):
        a = self.host(101, r"C:\GIS\a.aprx")
        b = self.host(202, r"C:\GIS\b.aprx")
        pid = os.getpid()
        with patch.object(pro_host, "state_path", side_effect=lambda port=None: str(self.root / f"port-{port}.json")):
            pro_host._write_state(a.state["port"], a.state["project"], "session_a", "secret_a", 1.0)
            pro_host._write_state(b.state["port"], b.state["project"], "session_b", "secret_b", 2.0)
            inst_a = self.root / f"arcgis-pro-mcp-instance-{pid}-session_a.json"
            inst_b = self.root / f"arcgis-pro-mcp-instance-{pid}-session_b.json"
            self.assertTrue(inst_a.exists())
            self.assertTrue(inst_b.exists())
            pro_host._clear_state("session_a")
            self.assertFalse((self.root / f"port-{a.state['port']}.json").exists())
            self.assertFalse(inst_a.exists())
            self.assertTrue((self.root / f"port-{b.state['port']}.json").exists())
            self.assertTrue(inst_b.exists())
            pro_host._clear_state("session_b")
            self.assertFalse((self.root / f"port-{b.state['port']}.json").exists())
            self.assertFalse(inst_b.exists())

    def test_single_window_restart_recovers_on_window_status_while_calls_fail_closed(self):
        first = self.host(101, r"C:\GIS\a.aprx")
        status1 = json.loads(server.arcgis_pro_window_status())
        self.assertTrue(status1["window_attached"])
        self.assertTrue(status1["target_confirmed"])
        self.assertEqual(status1["host_session_id"], first.state["session_id"])

        first.httpd.shutdown()
        first.httpd.server_close()
        first.thread.join(timeout=2)
        first.stopped = True
        first.path.unlink(missing_ok=True)
        restarted = self.host(101, r"C:\GIS\a.aprx")

        self.assertFalse(pro_attach.host_available())
        with self.assertRaisesRegex(RuntimeError, "未连接|不唯一|不会自动改连"):
            self.proxy()(aprx_path="CURRENT")
        self.assertEqual(restarted.calls, [])

        status2 = json.loads(server.arcgis_pro_window_status())
        self.assertTrue(status2["window_attached"])
        self.assertTrue(status2["target_confirmed"])
        self.assertEqual(status2["host_session_id"], restarted.state["session_id"])
        self.assertTrue(pro_attach.host_available())
        self.assertEqual(json.loads(self.proxy()(aprx_path="CURRENT"))["pid"], 101)
        self.assertEqual(len(restarted.calls), 1)

    def test_detach_window_clears_selection_and_recovers_for_next_host(self):
        self.host(101, r"C:\GIS\a.aprx")
        self.assertTrue(json.loads(server.arcgis_pro_window_status())["target_confirmed"])
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=False):
            detached = json.loads(server.arcgis_pro_detach_window())
        self.assertTrue(detached["detached"])
        self.assertFalse(pro_attach.host_available())
        empty_status = json.loads(server.arcgis_pro_window_status())
        self.assertFalse(empty_status["window_attached"])

        replacement = self.host(303, r"C:\GIS\new.aprx")
        recovered = json.loads(server.arcgis_pro_window_status())
        self.assertTrue(recovered["window_attached"])
        self.assertTrue(recovered["target_confirmed"])
        self.assertEqual(recovered["host_pid"], 303)
        self.assertEqual(recovered["host_session_id"], replacement.state["session_id"])

    def test_detach_specific_pid_preserves_other_window_and_disconnected_status_returns_cleanly(self):
        a, b = self.two_hosts()
        pro_attach.select_window(process_id=101)
        pro_attach.host_status(process_id=202, confirm_target=True)
        with patch.dict(os.environ, {"ARCGIS_PRO_MCP_ALLOW_WRITE": "1"}, clear=False):
            detached = json.loads(server.arcgis_pro_detach_window(process_id=202))
        self.assertTrue(detached["detached"])
        self.assertEqual(json.loads(self.proxy()(aprx_path="CURRENT"))["pid"], 101)
        self.assertEqual(len(a.calls), 1)
        self.assertEqual(b.calls, [])
        b_status = json.loads(server.arcgis_pro_window_status(process_id=202))
        self.assertFalse(b_status["window_attached"])

    def test_disconnected_selected_window_with_multiple_remaining_reports_ambiguity(self):
        a = self.host(101, r"C:\GIS\a.aprx")
        self.host(202, r"C:\GIS\b.aprx")
        self.host(303, r"C:\GIS\c.aprx")
        pro_attach.select_window(process_id=101)
        a.httpd.shutdown()
        a.httpd.server_close()
        a.thread.join(timeout=2)
        a.stopped = True
        a.path.unlink(missing_ok=True)
        self.assertFalse(pro_attach.host_available())
        status = json.loads(server.arcgis_pro_window_status())
        self.assertFalse(status["window_attached"])
        self.assertTrue(status["window_ambiguous"])
        self.assertEqual(status["window_count"], 2)

    def test_configured_host_port_pins_discovery_selection_and_explicit_pid(self):
        a, b = self.two_hosts()
        with patch.dict(os.environ, {pro_attach.ENV_PORT: str(a.state["port"])}, clear=False):
            listing = pro_attach.list_windows()
            self.assertEqual(listing["window_count"], 1)
            self.assertEqual(listing["windows"][0]["process_id"], 101)
            with self.assertRaisesRegex(RuntimeError, "未唯一匹配"):
                pro_attach.select_window(process_id=202)
            with self.assertRaisesRegex(RuntimeError, "未连接|不唯一"):
                self.proxy()(aprx_path="CURRENT:202")
            health = pro_attach.host_health()
            self.assertEqual(health["pid"], 101)
            with self.assertRaisesRegex(RuntimeError, pro_attach.ENV_PORT):
                pro_attach._snapshot_endpoint(
                    {
                        "_endpoint_base": f"http://127.0.0.1:{b.state['port']}",
                        "_endpoint_token": b.state["token"],
                    }
                )
        self.assertEqual(b.calls, [])

    def test_loopback_requests_ignore_http_proxy_and_never_leak_token(self):
        a = self.host(101, r"C:\GIS\a.aprx")
        proxy_hits = []

        class TrapProxyHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                proxy_hits.append((self.path, dict(self.headers)))
                self.send_response(500)
                self.end_headers()

            def do_POST(self):
                proxy_hits.append((self.path, dict(self.headers)))
                self.send_response(500)
                self.end_headers()

        trap = ThreadingHTTPServer(("127.0.0.1", 0), TrapProxyHandler)
        trap.daemon_threads = True
        trap_port = trap.server_address[1]
        trap_thread = threading.Thread(
            target=trap.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )
        trap_thread.start()
        try:
            proxy_url = f"http://127.0.0.1:{trap_port}"
            with patch.dict(
                os.environ,
                {"HTTP_PROXY": proxy_url, "http_proxy": proxy_url, "NO_PROXY": "", "no_proxy": ""},
                clear=False,
            ):
                listing = pro_attach.list_windows()
                self.assertEqual(listing["window_count"], 1)
                pro_attach.select_window(process_id=101)
                result = json.loads(self.proxy()(aprx_path="CURRENT"))
                self.assertEqual(result["pid"], 101)
            self.assertEqual(proxy_hits, [])
            self.assertEqual(len(a.calls), 1)
        finally:
            trap.shutdown()
            trap.server_close()
            trap_thread.join(timeout=2)

    def test_host_health_does_not_probe_endpoint_twice(self):
        self.host(101, r"C:\GIS\a.aprx")
        with patch.object(pro_attach, "_probe_endpoint", wraps=pro_attach._probe_endpoint) as probe:
            health = pro_attach.host_health()
        self.assertIsNotNone(health)
        self.assertEqual(health["pid"], 101)
        self.assertEqual(probe.call_count, 1)

    def test_stale_dead_pid_files_are_pruned_without_harming_live_mock_host(self):
        live = self.host(101, r"C:\GIS\live.aprx")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            closed_port = int(sock.getsockname()[1])

        dead_pid = 99999992
        dead_session = "deadsessionreal01"
        stale_state = {
            "service": pro_attach.SERVICE_NAME,
            "protocol_version": pro_attach.PROTOCOL_VERSION,
            "package_version": pro_attach.PACKAGE_VERSION,
            "port": closed_port,
            "pid": dead_pid,
            "project": r"C:\GIS\crashed.aprx",
            "session_id": dead_session,
            "token": "dead-token",
        }
        dead_inst = self.root / f"arcgis-pro-mcp-instance-{dead_pid}-{dead_session}.json"
        dead_host = self.root / f"arcgis-pro-mcp-host-{closed_port}.json"
        write_private_json(dead_inst, stale_state, temp_tag=dead_session)
        write_private_json(dead_host, stale_state, temp_tag=f"h{dead_session}")

        with patch.object(pro_attach, "HEALTH_TIMEOUT", 0.05):
            listing = pro_attach.list_windows()
        self.assertEqual(listing["window_count"], 1)
        self.assertEqual(listing["windows"][0]["process_id"], 101)
        self.assertTrue(live.path.exists())
        self.assertFalse(dead_inst.exists())
        self.assertFalse(dead_host.exists())

    def test_stale_dead_pid_files_over_cap_are_pruned_before_enforcing_limit(self):
        live = self.host(101, r"C:\GIS\live.aprx")
        stale_paths = []
        for idx in range(132):
            dead_pid = 99000000 + idx * 4
            dead_session = f"deadsession{idx:04d}"
            stale_port = 20000 + idx
            stale_state = {
                "service": pro_attach.SERVICE_NAME,
                "protocol_version": pro_attach.PROTOCOL_VERSION,
                "package_version": pro_attach.PACKAGE_VERSION,
                "port": stale_port,
                "pid": dead_pid,
                "project": r"C:\GIS\crashed.aprx",
                "session_id": dead_session,
                "token": f"dead-token-{idx}",
            }
            inst_path = self.root / f"arcgis-pro-mcp-instance-{dead_pid}-{dead_session}.json"
            host_path = self.root / f"arcgis-pro-mcp-host-{stale_port}.json"
            write_private_json(inst_path, stale_state, temp_tag=dead_session)
            write_private_json(host_path, stale_state, temp_tag=f"h{idx}")
            stale_paths.extend([inst_path, host_path])

        real_probe = pro_attach._probe_endpoint
        with patch.object(
            pro_attach,
            "_probe_endpoint",
            side_effect=lambda ep: real_probe(ep) if ep.state.get("port") == live.state["port"] else None,
        ):
            listing = pro_attach.list_windows()

        self.assertEqual(listing["window_count"], 1)
        self.assertEqual(listing["windows"][0]["process_id"], 101)
        self.assertTrue(live.path.exists())
        self.assertTrue(all(not path.exists() for path in stale_paths))


if __name__ == "__main__":
    unittest.main()
