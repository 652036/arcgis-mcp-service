"""Small, dependency-free redaction helpers for public MCP responses.

The server intentionally does not redact every response wholesale: edit and
session references are opaque capabilities that callers must receive.  These
helpers are applied at trust boundaries such as data-source descriptions,
ArcPy exception text, live-host status, and publishing diagnostics.
"""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "credential",
    "authorization",
    "private_key",
    "privatekey",
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "account_key",
    "accountkey",
    "subscription_key",
    "subscriptionkey",
    "signature",
)

_LONG_KEY_TOKEN = (
    r"password(?:[_-]?utf8)?|passwd|secret|token|"
    r"api[_-]?key|access[_-]?key|account[_-]?key|"
    r"credential|authorization|private[_-]?key|subscription[_-]?key|signature"
)
_SHORT_KEY_TOKEN = r"pwd|sig"
_KEY_NAME = (
    rf"(?:[A-Za-z0-9_-]*?(?:{_LONG_KEY_TOKEN})"
    rf"|(?:[A-Za-z0-9_-]+[_-])?(?:{_SHORT_KEY_TOKEN}))"
)
_URL_USERINFO_RE = re.compile(
    r"(?i)(?P<scheme>\b[a-z][a-z0-9+.-]*://)(?:[^/@\s]+@)"
)
_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+")
_QUERY_SECRET_RE = re.compile(rf"(?i)([?&]{_KEY_NAME}=)[^&#\s]*")
_ASSIGNMENT_SECRET_RE = re.compile(
    rf"(?i)((?:\"{_KEY_NAME}\"|'{_KEY_NAME}'|(?<![A-Za-z0-9_-]){_KEY_NAME}\b)\s*[:=]\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^;,\s&}\]]+)"
)


def redact_text(value: Any) -> str:
    """Redact common credentials embedded in arbitrary diagnostic text."""

    text = str(value)
    text = _URL_USERINFO_RE.sub(r"\g<scheme>[REDACTED]@", text)
    text = _BEARER_RE.sub(lambda match: f"{match.group(1)} [REDACTED]", text)
    text = _QUERY_SECRET_RE.sub(r"\1[REDACTED]", text)
    return _ASSIGNMENT_SECRET_RE.sub(r"\1[REDACTED]", text)


def redact_sensitive(value: Any) -> Any:
    """Recursively redact secret-bearing keys and embedded secret strings."""

    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            rendered_key = str(key)
            lowered = rendered_key.casefold()
            if any(part in lowered for part in _SENSITIVE_KEY_PARTS):
                result[rendered_key] = "[REDACTED]"
            else:
                result[rendered_key] = redact_sensitive(item)
        return result
    if isinstance(value, (list, tuple, set)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text_values(value: Any) -> Any:
    """Redact embedded secrets in strings without masking capability-key fields."""

    if isinstance(value, dict):
        return {str(key): redact_text_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [redact_text_values(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def safe_error(value: Any, limit: int = 1000) -> str:
    """Return bounded, redacted exception text for a public response."""

    return redact_text(value)[: max(0, int(limit))]
