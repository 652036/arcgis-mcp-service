"""Validation for the deliberately small Arcade expression subset exposed by MCP."""

from __future__ import annotations

import re

_SAFE_FUNCTIONS = frozenset(
    {
        "abs",
        "capitalize",
        "ceil",
        "concatenate",
        "date",
        "dateonly",
        "decode",
        "defaultvalue",
        "floor",
        "iif",
        "isempty",
        "left",
        "lower",
        "max",
        "mid",
        "min",
        "number",
        "proper",
        "replace",
        "right",
        "round",
        "text",
        "timeonly",
        "trim",
        "upper",
        "when",
    }
)
_SAFE_WORDS = frozenset({"and", "or", "not", "true", "false", "null", "nan"})
_STRING_RE = re.compile(r'"(?:\\.|""|[^"\\])*"|\'(?:\\.|\'\'|[^\'\\])*\'')
_IDENTIFIER_RE = re.compile(r"\$?[A-Za-z_][A-Za-z0-9_]*")
_FEATURE_MEMBER_RE = re.compile(r"(?i)\$feature\s*\.\s*[A-Za-z_][A-Za-z0-9_]*")
_FEATURE_INDEX_RE = re.compile(r"(?i)\$feature\s*\[\s*(?:\"\"|'')\s*\]")


def validate_safe_arcade_expression(expression: str, *, maximum: int = 8000) -> str:
    """Accept only expression-form Arcade without dynamic or remote data access.

    This is intentionally narrower than the full language.  It permits field
    references, literals, operators, and a small pure-function allowlist.  It
    rejects comments/statements so lexical filtering cannot be bypassed by
    comment insertion, and it never accepts arbitrary function names.
    """

    value = str(expression or "").strip()
    if not value or len(value) > maximum or "\x00" in value:
        raise RuntimeError(f"expression 必须为 1–{maximum} 字符")
    if any(marker in value for marker in ("//", "/*", "*/", ";", "{", "}")):
        raise RuntimeError("仅允许单一 Arcade 表达式；禁止注释、语句和代码块")

    def hide_string(match: re.Match[str]) -> str:
        quote = match.group(0)[0]
        return quote + quote

    masked = _STRING_RE.sub(hide_string, value)
    if '"' in masked.replace('""', "") or "'" in masked.replace("''", ""):
        raise RuntimeError("Arcade expression 包含未闭合字符串")

    # Mask complete member/index references before validating remaining names.
    lexical = _FEATURE_MEMBER_RE.sub(" ", masked)
    lexical = _FEATURE_INDEX_RE.sub(" ", lexical)
    if re.search(r"\$", lexical):
        raise RuntimeError("仅允许通过 $feature.FIELD 或 $feature['FIELD'] 引用字段")

    for match in _IDENTIFIER_RE.finditer(lexical):
        token = match.group(0)
        lowered = token.casefold()
        if lowered in _SAFE_WORDS:
            continue
        tail = lexical[match.end() :].lstrip()
        if lowered in _SAFE_FUNCTIONS and tail.startswith("("):
            continue
        raise RuntimeError(f"Arcade expression 包含未允许的标识符或函数：{token!r}")
    return value
