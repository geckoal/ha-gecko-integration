"""Connectivity helpers for Gecko cloud-shadow updates."""

from __future__ import annotations

from typing import Any


UNKNOWN_CONNECTIVITY_VALUES = frozenset(("", "UNKNOWN"))


def connectivity_value(value: Any) -> str:
    """Return a normalized connectivity value."""
    if value is None:
        return "UNKNOWN"
    return str(value).strip().upper()


def preserve_known_connectivity_value(previous: Any, incoming: Any) -> Any:
    """Keep the last explicit status when a sparse update reports UNKNOWN.

    Gecko shadow delta messages frequently omit the ``connectivity_`` object.
    gecko-iot-client currently turns those missing fields into ``UNKNOWN``.
    Treat that sentinel as "not included" once an explicit value is known.
    """
    if (
        connectivity_value(incoming) in UNKNOWN_CONNECTIVITY_VALUES
        and connectivity_value(previous) not in UNKNOWN_CONNECTIVITY_VALUES
    ):
        return previous
    return incoming if incoming is not None else "UNKNOWN"


def is_fully_connected(
    transport_connected: bool,
    gateway_status: Any,
    vessel_status: Any,
) -> bool:
    """Return whether the cloud transport, gateway, and vessel are usable."""
    return bool(transport_connected) and connectivity_value(
        gateway_status
    ) == "CONNECTED" and connectivity_value(vessel_status) in ("RUNNING", "READY")
