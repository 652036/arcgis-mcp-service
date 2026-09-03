"""Constrained ArcGIS publishing helpers.

No ArcPy import occurs at module load.  Callers inject the ArcPy module and
must explicitly pass the target and the digest returned by the previous stage.
Credentials are intentionally not accepted by this API; ArcGIS Pro's signed-in
portal or an allowlisted ``.ags`` connection owns authentication.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from arcgis_pro_mcp.paths import (
    path_under_root,
    require_absolute,
    require_allow_public_share,
    require_allow_publish,
    require_allow_publish_overwrite,
    validate_input_path_optional,
    validate_new_output_in_export_root,
    validate_output_in_export_root,
)
from arcgis_pro_mcp.redaction import redact_sensitive as _redact

_PORTAL_ALLOWLIST_ENV = "ARCGIS_PRO_MCP_PORTAL_ALLOWLIST"
_SERVER_ALLOWLIST_ENV = "ARCGIS_PRO_MCP_SERVER_ALLOWLIST"
_EXPORT_ROOT_ENV = "ARCGIS_PRO_MCP_EXPORT_ROOT"
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,120}$")
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")
_INLINE_SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|access[_-]?token|token)\s*[:=]\s*[^\s,;]+"
)

_WEB_LAYER_COMBINATIONS = {
    "HOSTING_SERVER": {"FEATURE", "TILE", "VECTOR_TILE", "SCENE_LAYER"},
    "FEDERATED_SERVER": {"MAP_IMAGE"},
}
_GENERIC_COMBINATIONS = {
    "STANDALONE_SERVER": {"MAP_SERVICE", "GP_SERVICE", "IMAGE_SERVICE"},
    "FEDERATED_SERVER": {"WEB_TOOL", "WEB_IMAGERY_LAYER"},
}


def _required_text(value: Any, label: str, max_length: int = 1024) -> str:
    result = str(value or "").strip()
    if not result:
        raise RuntimeError(f"{label} 不能为空")
    if len(result) > max_length:
        raise RuntimeError(f"{label} 不能超过 {max_length} 个字符")
    return result


def _split_allowlist(env_name: str) -> list[str]:
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        raise RuntimeError(f"必须配置非空 {env_name} 后才能发布")
    return [item.strip().strip('"') for item in re.split(r"[;,\r\n]+", raw) if item.strip()]


def _canonical_https_url(value: str, label: str) -> str:
    raw = _required_text(value, label, 2048)
    parsed = urlsplit(raw)
    if parsed.scheme.lower() != "https" or not parsed.hostname:
        raise RuntimeError(f"{label} 必须是完整的 HTTPS URL")
    if parsed.username or parsed.password:
        raise RuntimeError(f"{label} 禁止包含内联用户名或密码")
    if parsed.query or parsed.fragment:
        raise RuntimeError(f"{label} 禁止包含查询参数或片段")
    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{label} 端口无效") from exc
    netloc = host if port in (None, 443) else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def _active_portal_url(arcpy: Any) -> str:
    getter = getattr(arcpy, "GetActivePortalURL", None)
    if not callable(getter):
        raise RuntimeError("当前 ArcPy 不支持 GetActivePortalURL")
    value = getter()
    if not value:
        raise RuntimeError("ArcGIS Pro 当前没有活动 Portal")
    return _canonical_https_url(str(value), "active_portal_url")


def _require_portal_allowed(portal_url: str) -> str:
    target = _canonical_https_url(portal_url, "portal_url")
    allowed = {
        _canonical_https_url(item, f"{_PORTAL_ALLOWLIST_ENV} 条目")
        for item in _split_allowlist(_PORTAL_ALLOWLIST_ENV)
    }
    if target not in allowed:
        raise RuntimeError(f"Portal 不在 {_PORTAL_ALLOWLIST_ENV} 白名单中：{target}")
    return target


def _require_server_url_allowed(server_url: str) -> str:
    target = _canonical_https_url(server_url, "server_url")
    allowed_urls = set()
    for item in _split_allowlist(_SERVER_ALLOWLIST_ENV):
        if item.lower().startswith("https://"):
            allowed_urls.add(_canonical_https_url(item, f"{_SERVER_ALLOWLIST_ENV} 条目"))
    if target not in allowed_urls:
        raise RuntimeError(f"Server URL 不在 {_SERVER_ALLOWLIST_ENV} 白名单中：{target}")
    return target


def _require_server_connection_allowed(server_connection: str) -> str:
    path = validate_input_path_optional(server_connection, "server_connection")
    if not isinstance(path, str):
        raise RuntimeError("server_connection 必须为受控的 .ags 文件路径")
    if not path.lower().endswith(".ags") or not os.path.isfile(path):
        raise RuntimeError(f"server_connection 必须是存在的 .ags 文件：{path}")
    target = os.path.normcase(os.path.realpath(path))
    allowed_paths = set()
    for item in _split_allowlist(_SERVER_ALLOWLIST_ENV):
        if not item.lower().startswith("https://"):
            require_absolute(item, f"{_SERVER_ALLOWLIST_ENV} 路径条目")
            allowed_paths.add(os.path.normcase(os.path.realpath(item)))
    if target not in allowed_paths:
        raise RuntimeError(f"Server 连接不在 {_SERVER_ALLOWLIST_ENV} 白名单中")
    return path


def _require_export_root() -> str:
    root = os.environ.get(_EXPORT_ROOT_ENV, "").strip().strip('"')
    if not root:
        raise RuntimeError(f"发布工件必须配置 {_EXPORT_ROOT_ENV}")
    require_absolute(root, _EXPORT_ROOT_ENV)
    return os.path.realpath(root)


def _publishing_artifact(path: str, label: str, suffix: str, *, must_exist: bool) -> str:
    root = _require_export_root()
    resolved = validate_output_in_export_root(path, label)
    if not resolved.lower().endswith(suffix):
        raise RuntimeError(f"{label} 必须以 {suffix} 结尾")
    if not path_under_root(resolved, root):
        raise RuntimeError(f"{label} 必须位于 {_EXPORT_ROOT_ENV} 内")
    if must_exist and not os.path.isfile(resolved):
        raise RuntimeError(f"{label} 不存在或不是文件：{resolved}")
    if not must_exist:
        resolved = validate_new_output_in_export_root(path, label)
        parent = os.path.dirname(resolved)
        if parent:
            os.makedirs(parent, exist_ok=True)
    return resolved


def _reject_inline_secret(label: str, value: str) -> str:
    text = str(value or "")
    if _INLINE_SECRET_RE.search(text):
        raise RuntimeError(f"{label} 禁止包含密码、secret 或 token")
    if "://" in text:
        parsed = urlsplit(text)
        if parsed.username or parsed.password:
            raise RuntimeError(f"{label} 禁止包含 URL 内联凭据")
    return text


def artifact_digest(path: str) -> dict[str, Any]:
    """Return a non-secret identity for a publishing artifact."""

    require_absolute(path, "artifact_path")
    root = _require_export_root()
    resolved = os.path.realpath(path)
    if not path_under_root(resolved, root):
        raise RuntimeError(f"artifact_path 必须位于 {_EXPORT_ROOT_ENV} 内")
    if not os.path.isfile(resolved):
        raise RuntimeError(f"工件不存在或不是文件：{resolved}")
    digest = hashlib.sha256()
    size = 0
    with open(resolved, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    return {"path": resolved, "sha256": digest.hexdigest(), "size_bytes": size}


def _require_digest(path: str, expected_sha256: str) -> dict[str, Any]:
    expected = _required_text(expected_sha256, "expected_sha256", 64)
    if not _SHA256_RE.fullmatch(expected):
        raise RuntimeError("expected_sha256 必须为 64 位十六进制 SHA-256")
    identity = artifact_digest(path)
    if identity["sha256"].lower() != expected.lower():
        raise RuntimeError("发布工件 SHA-256 与 expected_sha256 不一致，拒绝继续")
    return identity


def _gp_messages(result: Any) -> list[str]:
    messages = []
    count = getattr(result, "messageCount", 0)
    getter = getattr(result, "getMessage", None)
    if callable(getter):
        for index in range(int(count or 0)):
            messages.append(str(_redact(getter(index))))
    return messages


def portal_status(arcpy: Any) -> dict[str, Any]:
    """Report active portal state without returning a username or token."""

    active = _active_portal_url(arcpy)
    configured = bool(os.environ.get(_PORTAL_ALLOWLIST_ENV, "").strip())
    allowed = False
    if configured:
        try:
            allowed = _require_portal_allowed(active) == active
        except RuntimeError:
            allowed = False

    signed_in = False
    token_getter = getattr(arcpy, "GetSigninToken", None)
    if callable(token_getter):
        try:
            signed_in = bool(token_getter())
        except Exception:  # noqa: BLE001 - status remains useful when token lookup fails
            signed_in = False

    info: dict[str, Any] = {}
    info_getter = getattr(arcpy, "GetPortalInfo", None)
    if callable(info_getter):
        raw = info_getter(portal_URL=active) or {}
        if isinstance(raw, dict):
            for key in ("SSL_enabled", "organization", "organization_type", "organizationtype", "portal_version", "role"):
                if key in raw:
                    info[key] = _redact(raw[key])
    return {
        "active_portal_url": active,
        "signed_in": signed_in,
        "allowlist_configured": configured,
        "allowlisted": allowed,
        "portal_info": info,
    }


def _validate_target(
    arcpy: Any,
    server_type: str,
    *,
    portal_url: str,
    federated_server_url: str,
    server_connection: str,
) -> dict[str, str]:
    active = ""
    if server_type in {"HOSTING_SERVER", "FEDERATED_SERVER"}:
        active = _active_portal_url(arcpy)
        target_portal = _require_portal_allowed(portal_url or active)
        if active != target_portal:
            raise RuntimeError("portal_url 必须与 ArcGIS Pro 当前活动 Portal 完全一致")
    if server_type == "HOSTING_SERVER":
        return {"portal_url": active, "upload_target": "HOSTING_SERVER"}
    if server_type == "FEDERATED_SERVER":
        server_url = _require_server_url_allowed(federated_server_url)
        return {"portal_url": active, "server_url": server_url, "upload_target": server_url}
    connection = _require_server_connection_allowed(server_connection)
    return {"server_connection": connection, "upload_target": connection}


def _sharing_analysis(draft: Any) -> dict[str, Any]:
    for method_name in ("analyzeSDDraft", "analyzeForSharing", "analyze"):
        method = getattr(draft, method_name, None)
        if callable(method):
            return {"available": True, "method": method_name, "result": _redact(method())}
    return {
        "available": False,
        "method": "StageService",
        "result": "此 sharing draft 类型在 ArcPy 中没有独立 analyzer；StageService 会执行分析。",
    }


def analyze_sharing_draft(draft: Any) -> dict[str, Any]:
    require_allow_publish()
    return _sharing_analysis(draft)


def create_sharing_draft(
    arcpy: Any,
    source: Any,
    service_name: str,
    output_sddraft_path: str,
    *,
    server_type: str = "HOSTING_SERVER",
    service_type: str = "FEATURE",
    portal_url: str = "",
    federated_server_url: str = "",
    server_connection: str = "",
    sharing_level: str = "OWNER",
    groups: list[str] | None = None,
    overwrite_existing_service: bool = False,
    portal_folder: str = "",
    server_folder: str = "",
    summary: str = "",
    tags: list[str] | None = None,
    description: str = "",
    credits: str = "",
    use_limitations: str = "",
    copy_data_to_server: bool | None = None,
    analyze: bool = True,
) -> dict[str, Any]:
    """Create, constrain, optionally analyze, and export one sharing draft."""

    require_allow_publish()
    name = _required_text(service_name, "service_name", 120)
    if not _SERVICE_NAME_RE.fullmatch(name):
        raise RuntimeError("service_name 只能包含字母、数字、下划线，且不超过 120 个字符")
    server_kind = _required_text(server_type, "server_type", 32).upper()
    service_kind = _required_text(service_type, "service_type", 64).upper()
    allowed_services = _WEB_LAYER_COMBINATIONS.get(server_kind, set()) | _GENERIC_COMBINATIONS.get(
        server_kind, set()
    )
    if service_kind not in allowed_services:
        raise RuntimeError(f"不支持的 server_type/service_type 组合：{server_kind}/{service_kind}")
    target = _validate_target(
        arcpy,
        server_kind,
        portal_url=portal_url,
        federated_server_url=federated_server_url,
        server_connection=server_connection,
    )
    level = _required_text(sharing_level, "sharing_level", 32).upper()
    if level not in {"OWNER", "ORGANIZATION", "EVERYONE"}:
        raise RuntimeError("sharing_level 须为 OWNER、ORGANIZATION 或 EVERYONE")
    if level == "EVERYONE":
        require_allow_public_share()
    if overwrite_existing_service:
        require_allow_publish_overwrite()
    if server_kind == "STANDALONE_SERVER" and (level != "OWNER" or groups):
        raise RuntimeError("STANDALONE_SERVER 不支持 Portal sharing_level/groups")

    strings = {
        "portal_folder": portal_folder,
        "server_folder": server_folder,
        "summary": summary,
        "description": description,
        "credits": credits,
        "use_limitations": use_limitations,
    }
    for label, value in strings.items():
        _reject_inline_secret(label, value)
    clean_groups = [_required_text(item, "groups[]", 256) for item in (groups or [])]
    clean_tags = [_required_text(item, "tags[]", 128) for item in (tags or [])]
    for index, value in enumerate(clean_groups):
        _reject_inline_secret(f"groups[{index}]", value)
    for index, value in enumerate(clean_tags):
        _reject_inline_secret(f"tags[{index}]", value)

    web_layer_draft = service_kind in _WEB_LAYER_COMBINATIONS.get(server_kind, set())
    if web_layer_draft:
        factory = getattr(source, "getWebLayerSharingDraft", None)
        if not callable(factory):
            raise RuntimeError("source 不支持 getWebLayerSharingDraft")
        draft = factory(server_kind, service_kind, name)
    else:
        draft_source = source
        if service_kind in {"IMAGE_SERVICE", "WEB_IMAGERY_LAYER"} and isinstance(source, str):
            draft_source = validate_input_path_optional(source, "source")
        sharing_module = getattr(arcpy, "sharing", None)
        factory = getattr(sharing_module, "CreateSharingDraft", None)
        if not callable(factory):
            raise RuntimeError("当前 ArcPy 不支持 arcpy.sharing.CreateSharingDraft")
        draft = factory(server_kind, service_kind, name, draft_source)
    if draft is None:
        raise RuntimeError("ArcPy 未返回 sharing draft 对象")

    property_values = {
        "summary": str(summary or ""),
        "tags": ",".join(clean_tags),
        "description": str(description or ""),
        "credits": str(credits or ""),
        "useLimitations": str(use_limitations or ""),
    }
    for property_name, value in property_values.items():
        if value:
            if not hasattr(draft, property_name):
                raise RuntimeError(f"当前 sharing draft 不支持属性 {property_name}")
            setattr(draft, property_name, value)
    if portal_folder:
        if not hasattr(draft, "portalFolder"):
            raise RuntimeError("当前 sharing draft 不支持 portalFolder")
        draft.portalFolder = portal_folder
    if server_folder:
        if not hasattr(draft, "serverFolder"):
            raise RuntimeError("当前 sharing draft 不支持 serverFolder")
        draft.serverFolder = server_folder
    if server_kind == "FEDERATED_SERVER" and web_layer_draft:
        if not hasattr(draft, "federatedServerUrl"):
            raise RuntimeError("当前 sharing draft 不支持 federatedServerUrl，无法绑定白名单服务器")
        draft.federatedServerUrl = target["server_url"]
    if not web_layer_draft:
        if not hasattr(draft, "targetServer"):
            raise RuntimeError("当前 sharing draft 不支持 targetServer，无法绑定受控发布目标")
        draft.targetServer = target["upload_target"]
    if hasattr(draft, "overwriteExistingService"):
        draft.overwriteExistingService = bool(overwrite_existing_service)
    elif overwrite_existing_service:
        raise RuntimeError("当前 sharing draft 不支持 overwriteExistingService")
    if copy_data_to_server is not None:
        if not hasattr(draft, "copyDataToServer"):
            raise RuntimeError("当前 sharing draft 不支持 copyDataToServer")
        draft.copyDataToServer = bool(copy_data_to_server)
    sharing = getattr(draft, "sharing", None)
    if level != "OWNER" or clean_groups:
        if sharing is None:
            raise RuntimeError("当前 sharing draft 不支持 Portal sharing 设置")
    if sharing is not None:
        sharing.sharingLevel = level
        sharing.groups = ",".join(clean_groups)

    out = _publishing_artifact(output_sddraft_path, "output_sddraft_path", ".sddraft", must_exist=False)
    exporter = getattr(draft, "exportToSDDraft", None)
    if not callable(exporter):
        raise RuntimeError("当前 sharing draft 不支持 exportToSDDraft")
    exporter(out)
    identity = artifact_digest(out)
    analysis = _sharing_analysis(draft) if analyze else {"available": False, "method": "disabled", "result": None}
    return {
        "artifact": identity,
        "server_type": server_kind,
        "service_type": service_kind,
        "service_name": name,
        "sharing_level": level,
        "overwrite_existing_service": bool(overwrite_existing_service),
        "target": {
            key: value
            for key, value in target.items()
            if key not in {"server_connection", "upload_target"}
        },
        "analysis": analysis,
    }


def stage_service_definition(
    arcpy: Any,
    sddraft_path: str,
    output_sd_path: str,
    *,
    expected_sha256: str,
    sharing_level: str = "OWNER",
    overwrite_existing_service: bool = False,
    staging_version: int | None = None,
) -> dict[str, Any]:
    """Analyze and stage an attested ``.sddraft`` into an ``.sd`` artifact."""

    require_allow_publish()
    level = _required_text(sharing_level, "sharing_level", 32).upper()
    if level not in {"OWNER", "ORGANIZATION", "EVERYONE"}:
        raise RuntimeError("sharing_level 须为 OWNER、ORGANIZATION 或 EVERYONE")
    if level == "EVERYONE":
        require_allow_public_share()
    if overwrite_existing_service:
        require_allow_publish_overwrite()
    source = _publishing_artifact(sddraft_path, "sddraft_path", ".sddraft", must_exist=True)
    source_identity = _require_digest(source, expected_sha256)
    out = _publishing_artifact(output_sd_path, "output_sd_path", ".sd", must_exist=False)
    server_module = getattr(arcpy, "server", None)
    stage = getattr(server_module, "StageService", None)
    if not callable(stage):
        raise RuntimeError("当前 ArcPy 不支持 arcpy.server.StageService")
    if staging_version is None:
        result = stage(source, out)
    else:
        version = int(staging_version)
        if version <= 0:
            raise RuntimeError("staging_version 必须为正整数")
        result = stage(source, out, version)
    output_identity = artifact_digest(out)
    return {
        "input_artifact": source_identity,
        "artifact": output_identity,
        "sharing_level": level,
        "overwrite_existing_service": bool(overwrite_existing_service),
        "analyzer_messages": _gp_messages(result),
    }


def publish_service_definition(
    arcpy: Any,
    sd_path: str,
    *,
    expected_sha256: str,
    server_type: str = "HOSTING_SERVER",
    portal_url: str = "",
    federated_server_url: str = "",
    server_connection: str = "",
    sharing_level: str = "OWNER",
    overwrite_existing_service: bool = False,
) -> dict[str, Any]:
    """Upload an attested service definition to one allowlisted target."""

    require_allow_publish()
    server_kind = _required_text(server_type, "server_type", 32).upper()
    if server_kind not in {"HOSTING_SERVER", "FEDERATED_SERVER", "STANDALONE_SERVER"}:
        raise RuntimeError("server_type 须为 HOSTING_SERVER、FEDERATED_SERVER 或 STANDALONE_SERVER")
    level = _required_text(sharing_level, "sharing_level", 32).upper()
    if level not in {"OWNER", "ORGANIZATION", "EVERYONE"}:
        raise RuntimeError("sharing_level 须为 OWNER、ORGANIZATION 或 EVERYONE")
    if level == "EVERYONE":
        require_allow_public_share()
    if overwrite_existing_service:
        require_allow_publish_overwrite()
    if server_kind == "STANDALONE_SERVER" and level != "OWNER":
        raise RuntimeError("STANDALONE_SERVER 不支持 Portal sharing_level")
    target = _validate_target(
        arcpy,
        server_kind,
        portal_url=portal_url,
        federated_server_url=federated_server_url,
        server_connection=server_connection,
    )
    source = _publishing_artifact(sd_path, "sd_path", ".sd", must_exist=True)
    identity = _require_digest(source, expected_sha256)
    server_module = getattr(arcpy, "server", None)
    upload = getattr(server_module, "UploadServiceDefinition", None)
    if not callable(upload):
        raise RuntimeError("当前 ArcPy 不支持 arcpy.server.UploadServiceDefinition")
    result = upload(source, target["upload_target"])
    return {
        "published": True,
        "artifact": identity,
        "server_type": server_kind,
        "sharing_level": level,
        "overwrite_existing_service": bool(overwrite_existing_service),
        "target": {key: value for key, value in target.items() if key not in {"server_connection", "upload_target"}},
        "messages": _gp_messages(result),
    }


__all__ = [
    "analyze_sharing_draft",
    "artifact_digest",
    "create_sharing_draft",
    "portal_status",
    "publish_service_definition",
    "stage_service_definition",
]
