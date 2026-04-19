"""Shared telemetry helpers for Gecko entities."""

from __future__ import annotations

from enum import Enum
import math
from typing import Any

from gecko_iot_client.models.flow_zone import FlowZoneInitiator
from gecko_iot_client.models.zone_types import ZoneType

FLOW_SPEED_MODE_OPTIONS: tuple[str, ...] = ("off", "low", "medium", "high", "max")
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
AUTOMATIC_TEMPERATURE_STATUS_NAMES: frozenset[str] = frozenset(
    {
        "HEATING",
        "COOLING",
        "HEAT_PUMP_HEATING",
        "HEAT_PUMP_AND_HEATER_HEATING",
        "HEAT_PUMP_COOLING",
        "HEAT_PUMP_DEFROSTING",
    }
)


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


def _uses_binary_near_max_speed_encoding(zone: Any) -> bool:
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

    if _uses_binary_near_max_speed_encoding(zone):
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

    if _uses_binary_near_max_speed_encoding(zone):
        return {
            "low": 99,
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
    if (
        FlowZoneInitiator.USER_DEMAND.value in initiators
        or FlowZoneInitiator.USER_DEMAND.name in initiators
    ):
        return "user_demand_initiator"

    if initiators & AUTOMATIC_FLOW_INITIATORS:
        return "automatic_initiator"

    if temperature_zones:
        status_names = get_temperature_status_names(temperature_zones)
        if status_names & AUTOMATIC_TEMPERATURE_STATUS_NAMES:
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

    if _uses_binary_near_max_speed_encoding(zone):
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
