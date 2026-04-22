"""Extract water-quality and other metrics from Gecko IoT device shadow state.

The official gecko-iot-client only models flow, lighting, and temperatureControl
zones. Waterlab and similar data typically appear under other ``zones.*`` keys or
nested under ``features``. This module walks those branches and collects numeric
leaves for Home Assistant sensors, without hard-coding unpublished field names.
"""

from __future__ import annotations

import math
import re
from typing import Any

# Zone families handled by gecko-iot-client (avoid duplicating climate/pumps/lights).
KNOWN_ZONE_TYPES = frozenset({"flow", "lighting", "temperatureControl"})

# Keys at reported root that are not walked for extension metrics (noise / non-chemistry).
SKIP_REPORTED_ROOT = frozenset({"connectivity_"})

_MAX_DEPTH = 12
_MAX_SENSORS = 64


def _get_reported(state_data: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize shadow payload to the ``reported`` object."""
    if not state_data or not isinstance(state_data, dict):
        return {}
    inner = state_data.get("state")
    if isinstance(inner, dict):
        reported = inner.get("reported")
        if isinstance(reported, dict):
            return reported
    reported = state_data.get("reported")
    if isinstance(reported, dict):
        return reported
    return {}


def _flatten_numeric(
    obj: Any,
    prefix: str,
    out: dict[str, float | int],
    depth: int,
) -> None:
    """Append numeric leaves to ``out`` keyed by dotted path."""
    if depth > _MAX_DEPTH or len(out) >= _MAX_SENSORS:
        return
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return
        out[prefix] = obj
        return
    if not isinstance(obj, dict):
        return
    for key, val in obj.items():
        if not isinstance(key, str):
            continue
        path = f"{prefix}.{key}" if prefix else key
        _flatten_numeric(val, path, out, depth + 1)


def extract_extension_metrics(
    state_data: dict[str, Any] | None,
) -> dict[str, float | int]:
    """Return path -> numeric value for unknown zone types and non-mode features."""
    reported = _get_reported(state_data)
    if not reported:
        return {}

    out: dict[str, float | int] = {}

    zones = reported.get("zones")
    if isinstance(zones, dict):
        for zone_type, zone_bundle in zones.items():
            if not isinstance(zone_type, str) or not isinstance(zone_bundle, dict):
                continue
            if zone_type in KNOWN_ZONE_TYPES:
                continue
            for zone_id, zone_state in zone_bundle.items():
                if not isinstance(zone_id, str):
                    continue
                base = f"zones.{zone_type}.{zone_id}"
                _flatten_numeric(zone_state, base, out, 0)

    features = reported.get("features")
    if isinstance(features, dict):
        for feat_key, feat_val in features.items():
            if feat_key == "operationMode":
                continue
            if not isinstance(feat_key, str):
                continue
            _flatten_numeric(feat_val, f"features.{feat_key}", out, 0)

    for root_key, root_val in reported.items():
        if root_key in SKIP_REPORTED_ROOT or root_key in ("zones", "features"):
            continue
        if not isinstance(root_key, str):
            continue
        _flatten_numeric(root_val, root_key, out, 0)

    return out


def shadow_topology_summary(state_data: dict[str, Any] | None) -> dict[str, Any]:
    """Redacted structural summary for diagnostics (no large leaf values)."""
    reported = _get_reported(state_data)
    if not reported:
        return {"reported_keys": []}

    summary: dict[str, Any] = {
        "reported_top_level_keys": sorted(reported.keys()),
    }

    zones = reported.get("zones")
    if isinstance(zones, dict):
        summary["zones_zone_type_keys"] = sorted(zones.keys())
        unknown = sorted(
            k for k in zones if isinstance(k, str) and k not in KNOWN_ZONE_TYPES
        )
        summary["zones_unknown_types"] = unknown
        for zt in unknown[:8]:
            zb = zones.get(zt)
            if isinstance(zb, dict):
                summary[f"zones.{zt}_zone_ids"] = sorted(zb.keys())[:16]

    features = reported.get("features")
    if isinstance(features, dict):
        summary["features_keys"] = sorted(features.keys())

    return summary


def metric_path_to_entity_slug(path: str, max_len: int = 48) -> str:
    """Turn a metric path into a safe unique entity name suffix."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", path).strip("_").lower()
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "metric"


def infer_sensor_metadata(path: str) -> tuple[str | None, str | None]:
    """Return (device_class, native_unit_of_measurement) heuristics from path."""
    lower = path.lower()
    device_class: str | None = None
    unit: str | None = None

    if re.search(r"(^|\.|_)ph($|\.|_)", lower) or lower.endswith("ph"):
        device_class = "ph"
    elif "orp" in lower or "oxidation" in lower:
        # Millivolts are not HA ``SensorDeviceClass.VOLTAGE`` (SI volts).
        unit = "mV"
    elif "temperature" in lower or lower.endswith("_temp") or "temp_" in lower:
        device_class = "temperature"
    elif "chlorine" in lower or "bromine" in lower or "sanitizer" in lower:
        unit = "ppm"
    elif "tds" in lower or "salinity" in lower:
        unit = "ppm"

    return device_class, unit
