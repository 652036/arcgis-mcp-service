"""Local-locator geocoding wrappers with explicit credit and path boundaries."""

from __future__ import annotations

from typing import Any

from arcgis_pro_mcp.dataset_management import verify_output_dataset
from arcgis_pro_mcp.paths import (
    require_allow_write,
    require_gp_output_root_mandatory,
    validate_gp_output_path,
    validate_input_path_optional,
)

_LOCATION_TYPES = frozenset({"ADDRESS_LOCATION", "ROUTING_LOCATION"})
_OUTPUT_FIELDS = frozenset({"ALL", "LOCATION_ONLY", "MINIMAL", "MINIMAL_AND_USER"})
_FEATURE_TYPES = frozenset(
    {
        "SUBADDRESS",
        "POINT_ADDRESS",
        "PARCEL",
        "STREET_ADDRESS",
        "STREET_INTERSECTION",
        "STREET_NAME",
        "LOCALITY",
        "POSTAL",
        "POINT_OF_INTEREST",
        "DISTANCE_MARKER",
    }
)


def _local_locator(locator_path: str) -> str:
    raw = str(locator_path or "").strip()
    if raw.lower().startswith(("http://", "https://")):
        raise RuntimeError(
            "本工具只允许本地/受控连接文件 locator，拒绝可能消耗 Portal credits 的 URL locator"
        )
    locator = validate_input_path_optional(raw, "locator_path")
    return locator


def _required_text(value: Any, label: str, maximum: int = 512) -> str:
    text = str(value or "").strip()
    if not text or len(text) > maximum or "\x00" in text:
        raise RuntimeError(f"{label} 不能为空、包含 NUL 或超过 {maximum} 字符")
    return text


def _ensure_new_output(arcpy: Any, output_path: str) -> str:
    require_gp_output_root_mandatory()
    output = validate_gp_output_path(output_path, "out_feature_class")
    if output.lower().endswith(".shp"):
        raise RuntimeError("GeocodeAddresses/ReverseGeocode 不支持 shapefile 输出")
    exists = getattr(arcpy, "Exists", None)
    if callable(exists) and bool(exists(output)):
        raise RuntimeError("输出已存在；为防止覆盖，请使用新的 out_feature_class")
    return output


def _messages(result: Any) -> str:
    method = getattr(result, "getMessages", None)
    return str(method() or "")[:8000] if callable(method) else ""


def _spatial_reference(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "name": str(getattr(value, "name", "") or ""),
        "wkid": getattr(value, "factoryCode", None),
    }


def _field_names(values: Any) -> list[str]:
    return [str(getattr(value, "name", value)) for value in list(values or [])]


def locator_info(arcpy: Any, locator_path: str) -> dict[str, Any]:
    locator_path = _local_locator(locator_path)
    constructor = getattr(getattr(arcpy, "geocoding", None), "Locator", None)
    if not callable(constructor):
        raise RuntimeError("当前 ArcPy 不支持 arcpy.geocoding.Locator")
    locator = constructor(locator_path)
    result: dict[str, Any] = {"locator_path": locator_path}
    for source, target in (
        ("capabilities", "capabilities"),
        ("compatibilityVersion", "compatibility_version"),
        ("countryCode", "country_code"),
        ("languageCode", "language_code"),
        ("locatorType", "locator_type"),
        ("precisionType", "precision_type"),
        ("role", "role"),
    ):
        try:
            value = getattr(locator, source)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[target] = value
        else:
            result[target] = str(value)
    # ArcGIS Pro 3.6 exposes these as multilineInputFields and
    # batchOutputFields.  Retain fallbacks for older ArcPy builds.
    for sources, target in (
        (("multilineInputFields", "multilineAddressFields"), "multiline_address_fields"),
        (("batchOutputFields", "outputFields"), "output_fields"),
        (("reverseOutputFields",), "reverse_output_fields"),
    ):
        for source in sources:
            try:
                result[target] = _field_names(getattr(locator, source))
                break
            except Exception:  # noqa: BLE001
                continue
    try:
        result["spatial_reference"] = _spatial_reference(locator.spatialReference)
    except Exception:  # noqa: BLE001
        pass
    return result


def _address_field_mapping(
    arcpy: Any,
    in_table: str,
    mappings: list[dict[str, str]],
) -> str:
    if not mappings or len(mappings) > 64:
        raise RuntimeError("address_fields 必须为 1–64 项数组")
    available = {
        str(field.name).lower(): str(field.name)
        for field in list(arcpy.ListFields(in_table) or [])
    }
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(mappings):
        if not isinstance(item, dict) or set(item) != {"locator_field", "table_field"}:
            raise RuntimeError(
                f"address_fields[{index}] 必须且只可包含 locator_field/table_field"
            )
        locator_field = _required_text(
            item["locator_field"], f"address_fields[{index}].locator_field", 256
        )
        table_field_raw = _required_text(
            item["table_field"], f"address_fields[{index}].table_field", 256
        )
        table_field = available.get(table_field_raw.lower())
        if table_field is None:
            raise RuntimeError(f"address_fields[{index}] 未找到表字段：{table_field_raw}")
        if locator_field.lower() in seen:
            raise RuntimeError(f"locator_field 重复：{locator_field}")
        seen.add(locator_field.lower())
        def _field_info_token(value: str) -> str:
            if any(character.isspace() for character in value):
                return "'" + value.replace("'", "''") + "'"
            return value

        result.append(
            f"{_field_info_token(locator_field)} {_field_info_token(table_field)}"
        )
    # ArcGIS Pro 3.6's GeocodeAddresses rejects a Python list-of-lists with
    # the opaque error "Object: error in executing tool".  Its Field Info
    # parser reliably accepts the documented semicolon-delimited form.
    return ";".join(result)


def geocode_addresses(
    arcpy: Any,
    in_table: str,
    locator_path: str,
    address_fields: list[dict[str, str]],
    out_feature_class: str,
    *,
    country_codes: list[str] | None = None,
    location_type: str = "ADDRESS_LOCATION",
    categories: list[str] | None = None,
    output_fields: str = "MINIMAL_AND_USER",
) -> dict[str, Any]:
    require_allow_write()
    table = validate_input_path_optional(in_table, "in_table")
    locator = _local_locator(locator_path)
    output = _ensure_new_output(arcpy, out_feature_class)
    mapping = _address_field_mapping(arcpy, table, address_fields)
    location = _required_text(location_type, "location_type", 64).upper()
    if location not in _LOCATION_TYPES:
        raise RuntimeError("location_type 须为 ADDRESS_LOCATION/ROUTING_LOCATION")
    output_mode = _required_text(output_fields, "output_fields", 64).upper()
    if output_mode not in _OUTPUT_FIELDS:
        raise RuntimeError("output_fields 不受支持")
    countries = [
        _required_text(value, "country_code", 3).upper()
        for value in list(country_codes or [])
    ]
    category_values = [
        _required_text(value, "category", 128) for value in list(categories or [])
    ]
    result = arcpy.geocoding.GeocodeAddresses(
        table,
        locator,
        mapping,
        output,
        "STATIC",
        countries,
        location,
        category_values,
        output_mode,
    )
    return {
        "input_table": table,
        "locator_path": locator,
        "credit_consuming_service": False,
        "messages": _messages(result),
        "output": verify_output_dataset(arcpy, output),
    }


def reverse_geocode(
    arcpy: Any,
    in_features: str,
    locator_path: str,
    out_feature_class: str,
    *,
    feature_types: list[str] | None = None,
    location_type: str = "ADDRESS_LOCATION",
) -> dict[str, Any]:
    require_allow_write()
    features = validate_input_path_optional(in_features, "in_features")
    locator = _local_locator(locator_path)
    output = _ensure_new_output(arcpy, out_feature_class)
    location = _required_text(location_type, "location_type", 64).upper()
    if location not in _LOCATION_TYPES:
        raise RuntimeError("location_type 须为 ADDRESS_LOCATION/ROUTING_LOCATION")
    types = [
        _required_text(value, "feature_type", 64).upper()
        for value in list(feature_types or [])
    ]
    unsupported = [value for value in types if value not in _FEATURE_TYPES]
    if unsupported:
        raise RuntimeError(f"feature_types 不受支持：{unsupported}")
    result = arcpy.geocoding.ReverseGeocode(
        features,
        locator,
        output,
        "ADDRESS",
        None,
        types,
        location,
    )
    return {
        "input_features": features,
        "locator_path": locator,
        "credit_consuming_service": False,
        "messages": _messages(result),
        "output": verify_output_dataset(arcpy, output),
    }
