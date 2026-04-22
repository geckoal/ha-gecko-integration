"""Diagnostics support for Gecko integration."""

from __future__ import annotations

import logging
from typing import Any

from gecko_iot_client import GeckoIotClient
from gecko_iot_client.models.connectivity import ConnectivityStatus

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .connection_manager import async_get_connection_manager
from .shadow_metrics import shadow_topology_summary

_LOGGER = logging.getLogger(__name__)


def _get_gecko_client_info(gecko_client: GeckoIotClient) -> dict[str, Any]:
    """Get gecko client diagnostics."""
    try:
        client_info: dict[str, Any] = {
            "client_id": gecko_client.id,
            "is_connected": gecko_client.is_connected,
            "has_configuration": gecko_client._configuration is not None,
            "has_state": gecko_client._state is not None,
        }

        if gecko_client.connectivity_status:
            connectivity = gecko_client.connectivity_status
            client_info["connectivity"] = {
                "transport_connected": connectivity.transport_connected,
                "gateway_status": connectivity.gateway_status,
                "vessel_status": connectivity.vessel_status,
                "is_fully_connected": connectivity.is_fully_connected,
            }

        if gecko_client.operation_mode_controller:
            omc = gecko_client.operation_mode_controller
            client_info["operation_mode"] = {
                "mode": omc.operation_mode.value if omc.operation_mode else None,
                "mode_name": omc.mode_name,
                "is_energy_saving": omc.is_energy_saving,
            }

        if gecko_client._zones:
            client_info["zones"] = {
                zone_type.value: len(zones)
                for zone_type, zones in gecko_client._zones.items()
            }

        if gecko_client.transporter:
            transporter = gecko_client.transporter
            transporter_info: dict[str, Any] = {
                "type": type(transporter).__name__,
            }
            monitor_id = getattr(transporter, "monitor_id", None)
            if monitor_id:
                transporter_info["monitor_id"] = monitor_id
            mqtt_client = getattr(transporter, "_mqtt_client", None)
            if mqtt_client and hasattr(mqtt_client, "is_connected"):
                transporter_info["mqtt_connected"] = mqtt_client.is_connected()
            client_info["transporter"] = transporter_info

        state = getattr(gecko_client, "_state", None)
        if isinstance(state, dict):
            client_info["shadow_topology"] = shadow_topology_summary(state)
            cfg = getattr(gecko_client, "_configuration", None)
            if isinstance(cfg, dict):
                zc = cfg.get("zones")
                if isinstance(zc, dict):
                    client_info["configuration_zones_keys"] = sorted(zc.keys())

        return client_info
    except Exception:
        _LOGGER.exception("Error getting gecko client info")
        return {"error": "exception"}


def _get_connection_diagnostics(connection_manager: Any) -> dict[str, Any]:
    """Get connection manager diagnostics."""
    if not connection_manager:
        return {}

    connections: dict[str, Any] = {}
    for monitor_id, connection in connection_manager._connections.items():
        conn_data: dict[str, Any] = {
            "monitor_id": monitor_id,
            "vessel_name": connection.vessel_name,
            "is_connected": connection.is_connected,
            "callback_count": len(connection.update_callbacks),
        }

        if connection.connectivity_status:
            connectivity: ConnectivityStatus = connection.connectivity_status
            conn_data["connectivity_status"] = {
                "transport_connected": connectivity.transport_connected,
                "gateway_status": str(connectivity.gateway_status),
                "vessel_status": str(connectivity.vessel_status),
                "is_fully_connected": connectivity.is_fully_connected,
            }

        if connection.gecko_client:
            conn_data["gecko_client"] = _get_gecko_client_info(connection.gecko_client)

        connections[monitor_id] = conn_data

    return connections


def _get_vessel_coordinators_diagnostics(config_entry: ConfigEntry) -> list[dict[str, Any]]:
    """Summarize per-vessel coordinators from config entry runtime data."""
    out: list[dict[str, Any]] = []
    if not hasattr(config_entry, "runtime_data") or not config_entry.runtime_data:
        return out
    coordinators = getattr(config_entry.runtime_data, "coordinators", None)
    if not coordinators:
        return out

    for coord in coordinators:
        entry: dict[str, Any] = {
            "vessel_id": coord.vessel_id,
            "vessel_name": coord.vessel_name,
            "monitor_id": coord.monitor_id,
            "has_initial_zones": coord._has_initial_zones,
            "zone_types": [zt.value for zt in coord.get_all_zones().keys()],
            "shadow_extension_metric_count": len(coord._shadow_metric_values),
        }
        if coord._shadow_metric_values:
            entry["shadow_extension_metric_paths"] = sorted(
                coord._shadow_metric_values.keys()
            )[:48]
        out.append(entry)
    return out


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    connection_manager = await async_get_connection_manager(hass)

    diagnostics_data: dict[str, Any] = {
        "config_entry": {
            "entry_id": config_entry.entry_id,
            "title": config_entry.title,
            "domain": config_entry.domain,
            "state": config_entry.state.value,
        },
        "vessels": _get_vessel_coordinators_diagnostics(config_entry),
        "connections": _get_connection_diagnostics(connection_manager),
    }

    if hasattr(config_entry, "runtime_data") and config_entry.runtime_data:
        rd = config_entry.runtime_data
        diagnostics_data["runtime_data"] = {
            "api_client_type": type(getattr(rd, "api_client", None)).__name__,
            "coordinator_count": len(getattr(rd, "coordinators", []) or []),
        }

    return diagnostics_data
