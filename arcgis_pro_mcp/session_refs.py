"""Process-local opaque references for temporary ArcPy layer and table views."""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

_PREFIX = "arcgis-mcp-ref:"
_LOCK = threading.RLock()


@dataclass
class SessionReference:
    reference: str
    kind: str
    value: Any
    name: str
    source: str
    created_at: float

    def public(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "kind": self.kind,
            "name": self.name,
            "source": self.source,
            "created_at": self.created_at,
        }


_REFERENCES: dict[str, SessionReference] = {}


def is_reference(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def register(value: Any, *, kind: str, name: str, source: str) -> str:
    reference = _PREFIX + secrets.token_urlsafe(18)
    item = SessionReference(reference, kind, value, name, source, time.time())
    with _LOCK:
        _REFERENCES[reference] = item
    return reference


def resolve(reference: str, *, expected_kinds: set[str] | None = None) -> Any:
    with _LOCK:
        item = _REFERENCES.get(reference)
    if item is None:
        raise RuntimeError("临时对象引用不存在或所属 MCP/窗口宿主进程已重启")
    if expected_kinds and item.kind not in expected_kinds:
        raise RuntimeError(f"临时对象类型为 {item.kind!r}，期望 {sorted(expected_kinds)}")
    return item.value


def list_public() -> list[dict[str, Any]]:
    with _LOCK:
        return [item.public() for item in _REFERENCES.values()]


def release(arcpy: Any, reference: str) -> dict[str, Any]:
    with _LOCK:
        item = _REFERENCES.pop(reference, None)
    if item is None:
        raise RuntimeError("临时对象引用不存在或已释放")
    deleted = False
    try:
        arcpy.management.Delete(item.value)
        deleted = True
    except Exception:  # noqa: BLE001
        try:
            arcpy.management.Delete(item.name)
            deleted = True
        except Exception:  # noqa: BLE001
            pass
    result = item.public()
    result.update({"ok": True, "released": True, "arcpy_deleted": deleted})
    return result


def clear_for_tests() -> None:
    with _LOCK:
        _REFERENCES.clear()
