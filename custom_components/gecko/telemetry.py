"""Shared telemetry helpers for Gecko entities."""

from __future__ import annotations

from enum import Enum
import math
from typing import Any

from gecko_iot_client.models.flow_zone import FlowZoneInitiator
from gecko_iot_client.models.zone_types import ZoneType

FLOW_SPEED_MODE_OPTIONS: tuple[str, ...] = ("off", "low", "medium", "high", "max")
USER_FLOW_INITIATORS: frozenset[str] = frozenset(
    {
        FlowZoneInitiator.USER_DEMAND.name,
        FlowZoneInitiator.USER_DEMAND.value,
    }
)
AUTOMATIC_FLOW_INITIATORS: frozenset[str] = frozenset(
    {
        FlowZoneInitiator.CHECKFLOW.name,
        FlowZoneInitiator.CHECKFLOW.value,
        FlowZoneInitiator.FILTRATION.name,
        FlowZoneInitiator.FILTRATION.value,
        FlowZoneInitiator.HEATING.name,
        FlowZoneInitiator.HEATING.value,
        FlowZoneInitiator.HEAT_PUMP.name,
        FlowZoneInitiator.HEAT_PUMP.value,
        FlowZoneInitiator.PURGE.name,
        FlowZoneInitiator.PURGE.value,
        FlowZoneInitiator.COOLDOWN.name,
        FlowZoneInitiator.COOLDOWN.value,
    }
)
HEATING_TEMPERATURE_STATUS_NAMES: frozenset[str] = frozenset(
    {
        "HEATING",
        "HEAT_PUMP_HEATING",
        "HEAT_PUMP_AND_HEATER_HEATING",
    }
)

DEVICE_TELEMETRY_KEYS: tuple[str, ...] = (
    "rf_signal_strength",
    "rf_channel",
    "pack_configuration",
    "home_firmware_version",
    "home_serial_number",
    "spa_firmware_version",
    "spa_serial_number",
)

RAW_API_REDACTED = "<redacted>"
_RAW_API_SENSITIVE_KEYS = {
    "authorization",
    "brokerurl",
    "clientsecret",
    "cookie",
    "privatekey",
}
_RAW_API_SENSITIVE_KEY_PARTS = (
    "apikey",
    "credential",
    "password",
    "secret",
    "token",
)


def _normalized_key(value: Any) -> str:
    """Normalize a shadow key so naming-style changes do not break telemetry."""
    return "".join(character for character in str(value).lower() if character.isalnum())


def redact_raw_api_payload(value: Any) -> Any:
    """Return a JSON-safe copy of a raw payload with credentials redacted."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, child in value.items():
            normalized_key = _normalized_key(key)
            if normalized_key in _RAW_API_SENSITIVE_KEYS or any(
                part in normalized_key for part in _RAW_API_SENSITIVE_KEY_PARTS
            ):
                redacted[str(key)] = RAW_API_REDACTED
            else:
                redacted[str(key)] = redact_raw_api_payload(child)
        return redacted

    if isinstance(value, (list, tuple)):
        return [redact_raw_api_payload(child) for child in value]

    if value is None or isinstance(value, (bool, float, int, str)):
        return value

    return str(value)


def _walk_mappings(value: Any, path: tuple[str, ...] = ()):
    """Yield mappings and their paths below a raw API value."""
    if isinstance(value, dict):
        yield value, path
        for key, child in value.items():
            yield from _walk_mappings(child, (*path, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_mappings(child, (*path, str(index)))


def _find_named_field(
    value: Any,
    aliases: set[str],
    path_prefix: tuple[str, ...] = (),
) -> tuple[Any, str | None]:
    """Return the first scalar and field path matching a normalized key alias."""
    for mapping, mapping_path in _walk_mappings(value, path_prefix):
        for key, candidate in mapping.items():
            if _normalized_key(key) not in aliases:
                continue
            field_path = ".".join((*mapping_path, str(key)))
            if not isinstance(candidate, (dict, list)):
                return candidate, field_path
            if isinstance(candidate, dict):
                for wrapper_key in ("value", "currentValue", "current", "default"):
                    if wrapper_key not in candidate:
                        continue
                    wrapped_value = candidate.get(wrapper_key)
                    if not isinstance(wrapped_value, (dict, list)):
                        return wrapped_value, f"{field_path}.{wrapper_key}"
    return None, None


def _find_module_mapping(
    value: Any,
    module_aliases: set[str],
    path_prefix: tuple[str, ...] = (),
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    """Find an EN/home or CO/spa module mapping in reported telemetry."""
    discriminator_keys = {
        "device",
        "devicetype",
        "module",
        "moduletype",
        "name",
        "type",
    }

    for mapping, mapping_path in _walk_mappings(value, path_prefix):
        for key, candidate in mapping.items():
            if _normalized_key(key) in module_aliases and isinstance(candidate, dict):
                return candidate, (*mapping_path, str(key))

        for key, candidate in mapping.items():
            if (
                _normalized_key(key) in discriminator_keys
                and _normalized_key(candidate) in module_aliases
            ):
                return mapping, mapping_path

    return None, ()


def _reported_shadow_state(
    spa_state: dict[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return the reported branch from either a full shadow or reported data."""
    if not isinstance(spa_state, dict):
        return {}, ()

    state = spa_state.get("state")
    if isinstance(state, dict):
        reported = state.get("reported")
        if isinstance(reported, dict):
            return reported, ("state", "reported")
        # AWS shadow deltas and document callbacks may provide the updated
        # fields directly below ``state`` instead of a reported branch.
        return state, ("state",)

    reported = spa_state.get("reported")
    if isinstance(reported, dict):
        return reported, ("reported",)

    return spa_state, ()


def _extract_device_telemetry(
    source_data: dict[str, Any] | None,
) -> dict[str, tuple[Any, str | None]]:
    """Extract telemetry values together with the raw field paths used."""
    reported, reported_path = _reported_shadow_state(source_data)

    rf_signal_strength = _find_named_field(
        reported,
        {
            "rfsignal",
            "rfsignalstrength",
            "rfstrength",
            "rssi",
        },
        reported_path,
    )
    rf_channel = _find_named_field(reported, {"rfchannel"}, reported_path)

    # Some shadow versions put generic signal/channel keys inside an RF object.
    rf_mapping, rf_path = _find_module_mapping(
        reported, {"rf", "radio"}, reported_path
    )
    telemetry_mapping, telemetry_path = _find_module_mapping(
        reported,
        {"devicetelemetry", "telemetry"},
        reported_path,
    )
    rf_source = rf_mapping or telemetry_mapping
    rf_source_path = rf_path if rf_mapping else telemetry_path
    if rf_source:
        if rf_signal_strength[0] is None:
            rf_signal_strength = _find_named_field(
                rf_source,
                {"signal", "signalquality", "signalstrength", "strength"},
                rf_source_path,
            )
        if rf_channel[0] is None:
            rf_channel = _find_named_field(
                rf_source,
                {"channel"},
                rf_source_path,
            )

    home_mapping, home_path = _find_module_mapping(
        reported,
        {
            "en",
            "enmodule",
            "gateway",
            "gatewaymodule",
            "home",
            "homemodule",
            "hometransmitter",
        },
        reported_path,
    )
    spa_mapping, spa_path = _find_module_mapping(
        reported,
        {
            "co",
            "comodule",
            "controller",
            "spa",
            "spamodule",
            "spacontroller",
            "spatransmitter",
        },
        reported_path,
    )

    firmware_aliases = {
        "firmware",
        "firmwareversion",
        "fw",
        "fwversion",
        "softwareversion",
        "version",
    }
    serial_aliases = {
        "serial",
        "serialnumber",
        "serialno",
        "sn",
    }

    telemetry = {
        "rf_signal_strength": rf_signal_strength,
        "rf_channel": rf_channel,
        "pack_configuration": (None, None),
        "home_firmware_version": (
            _find_named_field(home_mapping, firmware_aliases, home_path)
            if home_mapping
            else (None, None)
        ),
        "home_serial_number": (
            _find_named_field(home_mapping, serial_aliases, home_path)
            if home_mapping
            else (None, None)
        ),
        "spa_firmware_version": (
            _find_named_field(spa_mapping, firmware_aliases, spa_path)
            if spa_mapping
            else (None, None)
        ),
        "spa_serial_number": (
            _find_named_field(spa_mapping, serial_aliases, spa_path)
            if spa_mapping
            else (None, None)
        ),
    }

    # Also accept flattened fields such as enFirmwareVersion/coSerialNumber.
    flattened_aliases = {
        "home_firmware_version": {
            "enfirmware",
            "enfirmwareversion",
            "enfwversion",
            "gatewayfirmware",
            "gatewayfirmwareversion",
            "homefirmware",
            "homefirmwareversion",
        },
        "home_serial_number": {
            "enserial",
            "enserialnumber",
            "gatewayserial",
            "gatewayserialnumber",
            "homeserial",
            "homeserialnumber",
            "monitorid",
        },
        "spa_firmware_version": {
            "cofirmware",
            "cofirmwareversion",
            "cofwversion",
            "controllerfirmware",
            "controllerfirmwareversion",
            "spafirmware",
            "spafirmwareversion",
        },
        "spa_serial_number": {
            "coserial",
            "coserialnumber",
            "controllerserial",
            "controllerserialnumber",
            "spaserial",
            "spaserialnumber",
        },
    }
    for telemetry_key, aliases in flattened_aliases.items():
        if telemetry[telemetry_key][0] is None:
            telemetry[telemetry_key] = _find_named_field(
                reported, aliases, reported_path
            )

    # Gecko's MQTT configuration identifies the spa-side CO as ``vesselId``.
    # Do not treat the REST vessel API's cloud ``vesselId`` the same way; the
    # MQTT payload is distinguished by its configuration metadata and accessory
    # map, and carries a separate hardware-style identifier.
    configuration_metadata = reported.get("metadata")
    is_mqtt_configuration = (
        isinstance(configuration_metadata, dict)
        and configuration_metadata.get("configurationId")
        and isinstance(reported.get("accessories"), dict)
    )
    if is_mqtt_configuration:
        configuration_id = configuration_metadata.get("configurationId")
        if not isinstance(configuration_id, (dict, list)):
            configuration_path = ".".join(
                (*reported_path, "metadata", "configurationId")
            )
            telemetry["pack_configuration"] = (
                configuration_id,
                configuration_path,
            )

        if telemetry["spa_serial_number"][0] is None:
            mqtt_vessel_id = reported.get("vesselId")
            if (
                not isinstance(mqtt_vessel_id, (dict, list))
                and mqtt_vessel_id is not None
            ):
                vessel_id_path = ".".join((*reported_path, "vesselId"))
                telemetry["spa_serial_number"] = (
                    mqtt_vessel_id,
                    vessel_id_path,
                )

    return telemetry


def get_device_telemetry(
    spa_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """Extract RF and EN/CO device metadata from a Gecko shadow update.

    These values are not modeled by gecko-iot-client 1.0.3. Gecko shadows have
    used both camelCase and snake_case names, as well as keyed and list-based
    EN/CO module layouts, so extraction deliberately normalizes those variants.
    Missing values are returned as ``None`` so callers can retain values across
    sparse shadow delta messages.
    """
    return {
        key: value
        for key, (value, _source_path) in _extract_device_telemetry(spa_state).items()
    }


def get_device_telemetry_sources(
    source_data: dict[str, Any] | None,
) -> dict[str, str | None]:
    """Return the raw API field path used for each telemetry value."""
    return {
        key: source_path
        for key, (_value, source_path) in _extract_device_telemetry(source_data).items()
    }


def get_device_metadata_candidate_paths(
    source_data: dict[str, Any] | None,
) -> tuple[str, ...]:
    """Return raw field paths that may contain device metadata.

    Paths, rather than values, are exposed for troubleshooting so identifiers
    and other payload contents are not copied into diagnostics accidentally.
    """
    reported, reported_path = _reported_shadow_state(source_data)
    candidates: set[str] = set()
    exact_aliases = {"fw", "sn", "monitorid"}
    partial_aliases = {"firmware", "revision", "serial", "software", "version"}

    for mapping, mapping_path in _walk_mappings(reported, reported_path):
        for key, value in mapping.items():
            normalized_key = _normalized_key(key)
            if normalized_key not in exact_aliases and not any(
                alias in normalized_key for alias in partial_aliases
            ):
                continue

            field_path = ".".join((*mapping_path, str(key)))
            if not isinstance(value, (dict, list)):
                candidates.add(field_path)
                continue

            if isinstance(value, dict):
                for wrapper_key in ("value", "currentValue", "current", "default"):
                    if wrapper_key not in value:
                        continue
                    wrapped_value = value.get(wrapper_key)
                    if not isinstance(wrapped_value, (dict, list)):
                        candidates.add(f"{field_path}.{wrapper_key}")

    return tuple(sorted(candidates))


def retain_device_telemetry(
    current: dict[str, Any],
    source_data: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge explicitly reported telemetry into the last known values."""
    retained = {key: current.get(key) for key in DEVICE_TELEMETRY_KEYS}
    for key, value in get_device_telemetry(source_data).items():
        if value is not None:
            retained[key] = value
    return retained


def normalize_initiators(initiators: Any) -> set[str]:
    """Normalize flow initiators into comparable string values."""
    if not initiators:
        return set()

    normalized: set[str] = set()
    for initiator in initiators:
        if isinstance(initiator, Enum):
            normalized.add(str(initiator.name))
            normalized.add(str(initiator.value))
            continue

        initiator_text = str(initiator)
        normalized.add(initiator_text)
        normalized.add(initiator_text.upper())

    return normalized


def _as_float(value: Any) -> float | None:
    """Return a float for numeric values, otherwise None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def get_flow_speed_step_values(zone: Any) -> tuple[float, ...]:
    """Return the configured raw speed ladder for a flow zone."""
    speed_config = getattr(zone, "speed_config", None)
    if not isinstance(speed_config, dict):
        return ()

    minimum = _as_float(speed_config.get("minimum"))
    maximum = _as_float(speed_config.get("maximum"))
    step = _as_float(speed_config.get("stepIncrement"))
    if minimum is None or maximum is None or step is None:
        return ()
    if step <= 0 or maximum < minimum:
        return ()

    # Build the configured speed ladder and drop zero/off values.
    values: list[float] = []
    current = minimum
    for _ in range(16):
        if current > maximum + (step / 2):
            break
        if current > 0:
            rounded = round(current, 6)
            if rounded not in values:
                values.append(rounded)
        current += step

    return tuple(values)


def _reports_binary_near_max_speed_encoding(zone: Any) -> bool:
    """Return True when Gecko reports low/high as 99/100 style values."""
    if get_flow_speed_step_values(zone):
        return False

    speed = _as_float(getattr(zone, "speed", None))
    return speed is not None and 98.5 <= speed <= 100.5


def _get_mode_label_for_step_index(step_index: int, step_count: int) -> str:
    """Map a configured step index onto HA speed labels."""
    if step_count <= 1:
        return "high"
    if step_count == 2:
        return ("low", "high")[step_index]
    if step_count == 3:
        return ("low", "medium", "high")[step_index]

    normalized_index = round((step_index * 3) / (step_count - 1))
    return ("low", "medium", "high", "max")[normalized_index]


def get_supported_flow_speed_modes(zone: Any) -> tuple[str, ...]:
    """Return the HA speed labels supported by this flow zone."""
    step_values = get_flow_speed_step_values(zone)
    if step_values:
        ordered_modes: list[str] = []
        for step_index in range(len(step_values)):
            mode = _get_mode_label_for_step_index(step_index, len(step_values))
            if mode not in ordered_modes:
                ordered_modes.append(mode)
        return tuple(ordered_modes)

    if _reports_binary_near_max_speed_encoding(zone):
        return ("low", "high")

    return FLOW_SPEED_MODE_OPTIONS[1:]


def get_flow_speed_value_for_mode(zone: Any, mode: str) -> float | int | None:
    """Return the raw Gecko speed value for an HA speed mode."""
    if mode == "off":
        return 0

    step_values = get_flow_speed_step_values(zone)
    if step_values:
        matching_values = [
            value
            for step_index, value in enumerate(step_values)
            if _get_mode_label_for_step_index(step_index, len(step_values)) == mode
        ]
        if matching_values:
            return matching_values[len(matching_values) // 2]

    if _reports_binary_near_max_speed_encoding(zone):
        # The 99/100 values are a readback encoding for the two physical
        # states, not the percentages accepted by FlowZone.set_speed().
        return {
            "low": 50,
            "high": 100,
        }.get(mode)

    return {
        "low": 1,
        "medium": 2,
        "high": 3,
        "max": 4,
    }.get(mode)


def get_flow_speed_mode_for_percentage(zone: Any, percentage: int | None) -> str:
    """Map an HA percentage request to the closest supported flow mode."""
    supported_modes = get_supported_flow_speed_modes(zone)
    if not supported_modes:
        return "low"
    if percentage is None:
        return supported_modes[0]

    clamped_percentage = max(1, min(100, percentage))
    step_index = math.ceil((clamped_percentage / 100) * len(supported_modes)) - 1
    step_index = max(0, min(len(supported_modes) - 1, step_index))
    return supported_modes[step_index]


def _get_zone_runtime_state(
    spa_state: dict[str, Any] | None,
    zone_type: ZoneType,
    zone_id: Any,
) -> dict[str, Any]:
    """Return raw runtime state for a zone from the latest shadow payload."""
    if not spa_state:
        return {}

    state = spa_state.get("state", {})
    reported_state = state.get("reported", {}) if isinstance(state, dict) else {}
    desired_state = state.get("desired", {}) if isinstance(state, dict) else {}

    for branch in (reported_state, desired_state):
        if not isinstance(branch, dict):
            continue

        zone_state = (
            branch.get("zones", {})
            .get(zone_type.value, {})
            .get(str(zone_id), {})
        )
        if isinstance(zone_state, dict):
            return zone_state

    return {}


def get_flow_runtime_state(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the raw runtime state for a flow zone."""
    return _get_zone_runtime_state(
        spa_state,
        ZoneType.FLOW_ZONE,
        getattr(zone, "id", ""),
    )


def get_temperature_flow_status(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> Any:
    """Return the raw flow status reported for a temperature-control zone."""
    zone_state = _get_zone_runtime_state(
        spa_state,
        ZoneType.TEMPERATURE_CONTROL_ZONE,
        getattr(zone, "id", ""),
    )
    for key in ("flo_", "flo", "flowStatus", "flow_status"):
        if key in zone_state:
            return zone_state[key]
    return None


def get_flow_initiators(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> set[str]:
    """Return normalized flow initiators from raw shadow data or zone state."""
    zone_state = get_flow_runtime_state(zone, spa_state)
    raw_initiators = zone_state.get("initiators_")
    if raw_initiators is None:
        raw_initiators = zone_state.get("initiators")

    if raw_initiators is not None:
        return normalize_initiators(raw_initiators)

    return normalize_initiators(getattr(zone, "initiators_", None))


def get_non_user_flow_initiators(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> set[str]:
    """Return initiators that must not be stopped through manual pump control."""
    return get_flow_initiators(zone, spa_state) - USER_FLOW_INITIATORS


def is_user_controlled_flow_active(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> bool:
    """Return True when an active zone represents user-controllable demand."""
    if not getattr(zone, "active", False):
        return False

    initiators = get_flow_initiators(zone, spa_state)
    return not initiators or bool(initiators & USER_FLOW_INITIATORS)


def is_flow_safe_to_deactivate(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> bool:
    """Return whether stopping the zone cannot interrupt an automatic demand."""
    return not get_non_user_flow_initiators(zone, spa_state)


def is_filtration_flow_active(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> bool:
    """Return True when filtration is actively requesting this flow zone."""
    if not getattr(zone, "active", False):
        return False

    initiators = get_flow_initiators(zone, spa_state)
    return bool(
        {
            FlowZoneInitiator.FILTRATION.name,
            FlowZoneInitiator.FILTRATION.value,
        }
        & initiators
    )


def is_checkflow_active(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
) -> bool:
    """Return True during the spa's short automatic check-flow run."""
    if not getattr(zone, "active", False):
        return False

    initiators = get_flow_initiators(zone, spa_state)
    return bool(
        {
            FlowZoneInitiator.CHECKFLOW.name,
            FlowZoneInitiator.CHECKFLOW.value,
        }
        & initiators
    )


def get_temperature_status_names(temperature_zones: list[Any]) -> set[str]:
    """Return the normalized set of active temperature status names."""
    statuses: set[str] = set()
    for zone in temperature_zones:
        status = getattr(zone, "status", None)
        name = getattr(status, "name", None)
        if name:
            statuses.add(str(name))
    return statuses


def get_flow_manual_demand_reason(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
    temperature_zones: list[Any] | None = None,
) -> str:
    """Explain why a flow zone is or is not considered manual demand."""
    if not getattr(zone, "active", False):
        return "inactive"

    initiators = get_flow_initiators(zone, spa_state)
    if initiators & USER_FLOW_INITIATORS:
        return "user_demand_initiator"

    if initiators & AUTOMATIC_FLOW_INITIATORS:
        return "automatic_initiator"

    if temperature_zones:
        status_names = get_temperature_status_names(temperature_zones)
        if status_names & HEATING_TEMPERATURE_STATUS_NAMES:
            return "automatic_temperature_status"

    if initiators:
        return "unknown_initiator_fallback"

    return "no_initiator_fallback"


def is_manual_flow_demand(
    zone: Any,
    spa_state: dict[str, Any] | None = None,
    temperature_zones: list[Any] | None = None,
) -> bool:
    """Return True when the active flow zone was manually started by the user."""
    reason = get_flow_manual_demand_reason(zone, spa_state, temperature_zones)
    return reason in {"user_demand_initiator", "unknown_initiator_fallback", "no_initiator_fallback"}


def derive_flow_speed_mode(zone: Any) -> str | None:
    """Convert Gecko flow speed telemetry into an HA-friendly mode."""
    if not getattr(zone, "active", False):
        return "off"

    speed = _as_float(getattr(zone, "speed", None))
    if speed is None:
        return None

    if speed <= 0:
        return "off"

    step_values = get_flow_speed_step_values(zone)
    if step_values:
        nearest_step_index = min(
            range(len(step_values)),
            key=lambda index: abs(step_values[index] - speed),
        )
        return _get_mode_label_for_step_index(nearest_step_index, len(step_values))

    if _reports_binary_near_max_speed_encoding(zone):
        return "high" if speed >= 99.5 else "low"

    # Some spas report discrete preset indexes instead of percentages.
    if float(speed).is_integer() and 0 <= speed <= 4:
        return {
            0: "off",
            1: "low",
            2: "medium",
            3: "high",
            4: "max",
        }.get(int(speed))

    if speed < 34:
        return "low"
    if speed < 67:
        return "medium"
    return "high"


def derive_flow_percentage(zone: Any) -> int:
    """Convert Gecko flow telemetry into a stable HA percentage."""
    mode = derive_flow_speed_mode(zone)
    if mode == "off":
        return 0

    supported_modes = get_supported_flow_speed_modes(zone)
    if supported_modes and mode in supported_modes:
        mode_index = supported_modes.index(mode) + 1
        return int(round((mode_index / len(supported_modes)) * 100))

    speed = getattr(zone, "speed", None)
    if isinstance(speed, (int, float)):
        return max(0, min(100, int(speed)))
    return 0
