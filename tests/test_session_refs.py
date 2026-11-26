from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from arcgis_pro_mcp import _gp_common, da_read, session_refs


class _FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SessionRefsTests(unittest.TestCase):
    def setUp(self) -> None:
        session_refs.clear_for_tests()
        session_refs.configure(
            max_references=session_refs.DEFAULT_MAX_REFERENCES,
            ttl_seconds=session_refs.DEFAULT_TTL_SECONDS,
        )
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        session_refs.clear_for_tests()
        session_refs.configure(
            max_references=session_refs.DEFAULT_MAX_REFERENCES,
            ttl_seconds=session_refs.DEFAULT_TTL_SECONDS,
        )

    def test_register_resolve_list_and_release_lifecycle(self) -> None:
        obj = object()
        ref = session_refs.register(obj, kind="feature_layer", name="roads_lyr", source="C:/data/roads.shp")
        self.assertTrue(session_refs.is_reference(ref))
        self.assertFalse(session_refs.is_reference("not-a-ref"))
        self.assertIs(session_refs.resolve(ref, expected_kinds={"feature_layer"}), obj)

        with self.assertRaisesRegex(RuntimeError, "期望"):
            session_refs.resolve(ref, expected_kinds={"table_view"})

        public = session_refs.list_public()
        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]["reference"], ref)
        self.assertEqual(public[0]["kind"], "feature_layer")
        self.assertEqual(public[0]["name"], "roads_lyr")

        delete_mock = MagicMock()
        arcpy = SimpleNamespace(management=SimpleNamespace(Delete=delete_mock))
        released = session_refs.release(arcpy, ref)
        self.assertTrue(released["ok"])
        self.assertTrue(released["released"])
        self.assertTrue(released["arcpy_deleted"])
        delete_mock.assert_called_once_with(obj)

        with self.assertRaisesRegex(RuntimeError, "不存在"):
            session_refs.resolve(ref)

    def test_ttl_expiration_evicts_stale_references(self) -> None:
        clock = _FakeClock(500.0)
        with patch.object(session_refs.time, "monotonic", side_effect=clock):
            session_refs.configure(ttl_seconds=60.0)
            ref_a = session_refs.register("A", kind="feature_layer", name="a", source="src_a")
            clock.advance(30.0)
            ref_b = session_refs.register("B", kind="feature_layer", name="b", source="src_b")

            self.assertEqual(session_refs.resolve(ref_a), "A")
            self.assertEqual(session_refs.resolve(ref_b), "B")

            clock.advance(31.0)  # t=561: ref_a expired (age 61 >= 60), ref_b still alive (age 31 < 60)
            with self.assertRaisesRegex(RuntimeError, "不存在"):
                session_refs.resolve(ref_a)
            self.assertEqual(session_refs.resolve(ref_b), "B")
            self.assertEqual([item["reference"] for item in session_refs.list_public()], [ref_b])

            clock.advance(30.0)  # t=591: ref_b expired (age 61 >= 60)
            self.assertEqual(session_refs.prune_expired(), 1)
            self.assertEqual(session_refs.list_public(), [])

    def test_lru_eviction_respects_max_size_and_resolve_touches_recency(self) -> None:
        clock = _FakeClock(100.0)
        with patch.object(session_refs.time, "monotonic", side_effect=clock):
            session_refs.configure(max_references=3, ttl_seconds=3600.0)
            ref_a = session_refs.register("A", kind="feature_layer", name="a", source="sa")
            ref_b = session_refs.register("B", kind="feature_layer", name="b", source="sb")
            ref_c = session_refs.register("C", kind="feature_layer", name="c", source="sc")

            # Touch A so B becomes least recently used.
            self.assertEqual(session_refs.resolve(ref_a), "A")
            ref_d = session_refs.register("D", kind="feature_layer", name="d", source="sd")

            with self.assertRaisesRegex(RuntimeError, "不存在"):
                session_refs.resolve(ref_b)
            self.assertEqual(session_refs.resolve(ref_a), "A")
            self.assertEqual(session_refs.resolve(ref_c), "C")
            self.assertEqual(session_refs.resolve(ref_d), "D")
            self.assertEqual(len(session_refs.list_public()), 3)

    def test_expired_entries_pruned_before_lru_eviction(self) -> None:
        clock = _FakeClock(100.0)
        with patch.object(session_refs.time, "monotonic", side_effect=clock):
            session_refs.configure(max_references=2, ttl_seconds=10.0)
            ref_a = session_refs.register("A", kind="feature_layer", name="a", source="sa")
            clock.advance(5.0)
            ref_b = session_refs.register("B", kind="feature_layer", name="b", source="sb")
            # Touch ref_a so ref_b is older in LRU order, but advance so ref_a expires while ref_b is alive.
            session_refs.resolve(ref_a)
            clock.advance(6.0)  # age(ref_a)=11 (expired), age(ref_b)=6 (alive)
            ref_c = session_refs.register("C", kind="feature_layer", name="c", source="sc")

            self.assertEqual(session_refs.resolve(ref_b), "B")
            self.assertEqual(session_refs.resolve(ref_c), "C")
            with self.assertRaisesRegex(RuntimeError, "不存在"):
                session_refs.resolve(ref_a)

    def test_configure_validates_inputs_and_shrinks_immediately(self) -> None:
        with self.assertRaises(ValueError):
            session_refs.configure(max_references=0)
        with self.assertRaises(ValueError):
            session_refs.configure(max_references=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            session_refs.configure(ttl_seconds=0)
        with self.assertRaises(ValueError):
            session_refs.configure(ttl_seconds=float("nan"))

        refs = [
            session_refs.register(i, kind="feature_layer", name=f"n{i}", source=f"s{i}")
            for i in range(5)
        ]
        session_refs.configure(max_references=2)
        remaining = [item["reference"] for item in session_refs.list_public()]
        self.assertEqual(remaining, refs[-2:])

    def test_concurrent_register_and_resolve_remain_bounded(self) -> None:
        session_refs.configure(max_references=32, ttl_seconds=3600.0)
        errors: list[Exception] = []

        def worker(worker_id: int) -> None:
            try:
                for step in range(40):
                    ref = session_refs.register(
                        (worker_id, step),
                        kind="feature_layer",
                        name=f"w{worker_id}_{step}",
                        source="src",
                    )
                    try:
                        session_refs.resolve(ref)
                    except RuntimeError:
                        # Acceptable if evicted concurrently by another thread.
                        pass
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertLessEqual(len(session_refs.list_public()), 32)


class GpCommonAndDaReadTests(unittest.TestCase):
    def test_gp_messages_redacts_secrets_from_result_and_arcpy(self) -> None:
        class _SecretResult:
            messageCount = 2

            def getMessage(self, index: int) -> str:
                return [
                    "Connected https://admin:s3cr3t@gis.example.com/portal",
                    "Header Authorization: Bearer tok_12345 password=hunter2",
                ][index]

        redacted = _gp_common.gp_messages(_SecretResult())
        self.assertEqual(len(redacted), 2)
        self.assertNotIn("s3cr3t", redacted[0])
        self.assertNotIn("tok_12345", redacted[1])
        self.assertNotIn("hunter2", redacted[1])
        self.assertIn("[REDACTED]", redacted[0])
        self.assertIn("[REDACTED]", redacted[1])

        arcpy_mod = SimpleNamespace(
            GetMessageCount=lambda: 1,
            GetMessage=lambda _i: "token=abc123",
        )
        self.assertEqual(_gp_common.gp_messages(arcpy_mod), ["token=[REDACTED]"])
        self.assertEqual(_gp_common.gp_messages(None), [])

    def test_gp_common_validators(self) -> None:
        self.assertEqual(_gp_common.required_text("  abc  ", "name"), "abc")
        with self.assertRaisesRegex(RuntimeError, "name 不能为空"):
            _gp_common.required_text("   ", "name")
        with self.assertRaisesRegex(RuntimeError, "name 必须为字符串"):
            _gp_common.required_text(123, "name", strict_str=True)

        self.assertEqual(_gp_common.validated_enum(" mean ", frozenset({"MEAN", "SUM"}), "stat"), "MEAN")
        self.assertEqual(
            _gp_common.validated_enum("netcdf", {"NETCDF": "NetCDF", "TIFF": "TIFF"}, "fmt"),
            "NetCDF",
        )
        with self.assertRaisesRegex(RuntimeError, "stat 须为"):
            _gp_common.validated_enum("bad", frozenset({"MEAN"}), "stat")

        arcpy_ok = SimpleNamespace(Exists=lambda _p: True, TestSchemaLock=lambda _p: True)
        _gp_common.require_exists(arcpy_ok, "C:/a", "path", require_api=True)
        _gp_common.require_schema_lock(arcpy_ok, "C:/a", require_api=True)

        arcpy_no = SimpleNamespace(Exists=lambda _p: False, TestSchemaLock=lambda _p: False)
        with self.assertRaisesRegex(RuntimeError, "不存在"):
            _gp_common.require_exists(arcpy_no, "C:/a", "path")
        with self.assertRaisesRegex(RuntimeError, "方案锁"):
            _gp_common.require_schema_lock(arcpy_no, "C:/a")

    def test_da_read_calls_list_fields_only_once_per_query(self) -> None:
        fields = [
            SimpleNamespace(name="OBJECTID", type="OID"),
            SimpleNamespace(name="NAME", type="String"),
            SimpleNamespace(name="Shape", type="Geometry"),
        ]

        class _Cursor:
            def __init__(self, rows: list[tuple[object, ...]]) -> None:
                self._rows = rows

            def __enter__(self) -> _Cursor:
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def __iter__(self):
                return iter(self._rows)

        list_fields = MagicMock(return_value=fields)
        arcpy = SimpleNamespace(
            ListFields=list_fields,
            da=SimpleNamespace(
                SearchCursor=MagicMock(
                    side_effect=[
                        _Cursor([(1, "alpha")]),
                        _Cursor([(1, "alpha"), (2, "beta")]),
                        _Cursor([("alpha",), ("beta",), ("alpha",)]),
                    ]
                )
            ),
        )

        sampled = da_read.table_sample(arcpy, "C:/data.gdb/fc", ["OBJECTID", "NAME"])
        self.assertEqual(sampled, [{"OBJECTID": 1, "NAME": "alpha"}])
        self.assertEqual(list_fields.call_count, 1)

        queried = da_read.query_rows(arcpy, "C:/data.gdb/fc", ["OBJECTID", "NAME"], offset=1)
        self.assertEqual(queried, [{"OBJECTID": 2, "NAME": "beta"}])
        self.assertEqual(list_fields.call_count, 2)

        distinct = da_read.distinct_values(arcpy, "C:/data.gdb/fc", "NAME")
        self.assertEqual(distinct, ["alpha", "beta"])
        self.assertEqual(list_fields.call_count, 3)


if __name__ == "__main__":
    unittest.main()
