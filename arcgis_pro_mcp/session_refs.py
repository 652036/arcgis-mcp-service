"""Process-local opaque references for temporary ArcPy layer and table views."""

from __future__ import annotations

import math
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

_PREFIX = "arcgis-mcp-ref:"
_LOCK = threading.RLock()

DEFAULT_MAX_REFERENCES = 256
DEFAULT_TTL_SECONDS = 3600.0

_MAX_REFERENCES: int = DEFAULT_MAX_REFERENCES
_TTL_SECONDS: float = DEFAULT_TTL_SECONDS


@dataclass
class SessionReference:
    reference: str
    kind: str
    value: Any
    name: str
    source: str
    created_at: float
    created_monotonic: float = field(default_factory=time.monotonic, repr=False)
    last_accessed_at: float = field(default_factory=time.time, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "reference": self.reference,
            "kind": self.kind,
            "name": self.name,
            "source": self.source,
            "created_at": self.created_at,
        }


_REFERENCES: OrderedDict[str, SessionReference] = OrderedDict()


def _prune_expired_locked(now: float) -> list[SessionReference]:
    expired_keys = [
        key
        for key, item in _REFERENCES.items()
        if now - item.created_monotonic >= _TTL_SECONDS
    ]
    evicted: list[SessionReference] = []
    for key in expired_keys:
        item = _REFERENCES.pop(key, None)
        if item is not None:
            evicted.append(item)
    return evicted


def _evict_overflow_locked(target_size: int) -> list[SessionReference]:
    evicted: list[SessionReference] = []
    while len(_REFERENCES) > target_size:
        _key, item = _REFERENCES.popitem(last=False)
        evicted.append(item)
    return evicted


def configure(
    *,
    max_references: int | None = None,
    ttl_seconds: float | None = None,
) -> tuple[int, float]:
    """Configure bounded capacity and TTL for session references."""
    global _MAX_REFERENCES, _TTL_SECONDS
    if max_references is not None:
        if (
            isinstance(max_references, bool)
            or not isinstance(max_references, int)
            or max_references < 1
        ):
            raise ValueError("max_references must be a positive integer")
    if ttl_seconds is not None:
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, (int, float))
            or not math.isfinite(ttl_seconds)
            or ttl_seconds <= 0
        ):
            raise ValueError("ttl_seconds must be a positive finite number")
    with _LOCK:
        if max_references is not None:
            _MAX_REFERENCES = max_references
        if ttl_seconds is not None:
            _TTL_SECONDS = float(ttl_seconds)
        _prune_expired_locked(time.monotonic())
        _evict_overflow_locked(_MAX_REFERENCES)
        return _MAX_REFERENCES, _TTL_SECONDS


def prune_expired() -> int:
    """Remove all expired references and return the number evicted."""
    with _LOCK:
        return len(_prune_expired_locked(time.monotonic()))


def is_reference(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(_PREFIX)


def register(value: Any, *, kind: str, name: str, source: str) -> str:
    reference = _PREFIX + secrets.token_urlsafe(18)
    now_wall = time.time()
    now_mono = time.monotonic()
    item = SessionReference(
        reference,
        kind,
        value,
        name,
        source,
        now_wall,
        created_monotonic=now_mono,
        last_accessed_at=now_wall,
    )
    with _LOCK:
        _prune_expired_locked(now_mono)
        _evict_overflow_locked(max(0, _MAX_REFERENCES - 1))
        _REFERENCES[reference] = item
    return reference


def resolve(reference: str, *, expected_kinds: set[str] | None = None) -> Any:
    now_mono = time.monotonic()
    with _LOCK:
        _prune_expired_locked(now_mono)
        item = _REFERENCES.get(reference)
        if item is None:
            raise RuntimeError("临时对象引用不存在或所属 MCP/窗口宿主进程已重启")
        if expected_kinds and item.kind not in expected_kinds:
            raise RuntimeError(f"临时对象类型为 {item.kind!r}，期望 {sorted(expected_kinds)}")
        _REFERENCES.move_to_end(reference)
        item.last_accessed_at = time.time()
        return item.value


def list_public() -> list[dict[str, Any]]:
    with _LOCK:
        _prune_expired_locked(time.monotonic())
        return [item.public() for item in _REFERENCES.values()]


def release(arcpy: Any, reference: str) -> dict[str, Any]:
    with _LOCK:
        _prune_expired_locked(time.monotonic())
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
