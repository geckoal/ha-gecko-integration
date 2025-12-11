"""Diagnostics support for Gecko integration."""

from __future__ import annotations

import logging
from typing import Any

from gecko_iot_client import GeckoIotClient
from gecko_iot_client.models.connectivity import ConnectivityStatus
from gecko_iot_client.models.zone_types import ZoneType

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .connection_manager import async_get_connection_manager

_LOGGER = logging.getLogger(__name__)


def _get_coordinator_diagnostics(coordinator) -> dict[str, Any]:
    """Get coordinator diagnostics."""
    if not coordinator:
        return {}
    
    return {
        "managed_monitors": list(coordinator._managed_monitors),
        "monitors_with_zones": list(coordinator._monitors_with_zones),
        "zones_by_monitor": {
            monitor_id: {
                zone_type.value: len(zones) 
                for zone_type, zones in monitor_zones.items()
            }
            for monitor_id, monitor_zones in coordinator._zones_by_monitor.items()
        },
        "spa_states": {
            monitor_id: list(state.keys()) if state else []
            for monitor_id, state in coordinator._spa_states.items()
        },
    }


def _get_gecko_client_info(gecko_client: GeckoIotClient) -> dict[str, Any]:
    """Get gecko client diagnostics."""
    try:
        client_info = {
            "client_id": gecko_client.id,
            "is_connected": gecko_client.is_connected,
            "has_configuration": gecko_client._configuration is not None,
            "has_state": gecko_client._state is not None,
        }
        
        # Add connectivity status details
        if gecko_client.connectivity_status:
            connectivity = gecko_client.connectivity_status
            client_info["connectivity"] = {
                "transport_connected": connectivity.transport_connected,
                "gateway_status": connectivity.gateway_status,
                "vessel_status": connectivity.vessel_status,
                "is_fully_connected": connectivity.is_fully_connected,
            }
        
        # Add operation mode information
        if gecko_client.operation_mode_controller:
            omc = gecko_client.operation_mode_controller
            client_info["operation_mode"] = {
                "mode": omc.operation_mode.value if omc.operation_mode else None,
                "mode_name": omc.mode_name,
                "is_energy_saving": omc.is_energy_saving,
            }
        
        # Add zone information
        if gecko_client._zones:
            client_info["zones"] = {
                zone_type.value: len(zones)
                for zone_type, zones in gecko_client._zones.items()
            }
        
        # Add transporter information
        if gecko_client.transporter:
            transporter = gecko_client.transporter
            transporter_info = {
                "type": type(transporter).__name__,
            }
            # Check for monitor_id using getattr to handle different transporter types
            monitor_id = getattr(transporter, 'monitor_id', None)
            if monitor_id:
                transporter_info["monitor_id"] = monitor_id
            # Check for MQTT-specific attributes
            mqtt_client = getattr(transporter, '_mqtt_client', None)
            if mqtt_client and hasattr(mqtt_client, 'is_connected'):
                transporter_info["mqtt_connected"] = mqtt_client.is_connected()
            client_info["transporter"] = transporter_info
        
        return client_info
    except Exception as e:
        _LOGGER.exception("Error getting gecko client info")
        return {"error": str(e)}


def _get_connection_diagnostics(connection_manager) -> dict[str, Any]:
    """Get connection diagnostics."""
    if not connection_manager:
        return {}
    
    connections = {}
    for monitor_id, connection in connection_manager._connections.items():
        conn_data = {
            "monitor_id": monitor_id,
            "vessel_name": connection.vessel_name,
            "is_connected": connection.is_connected,
            "websocket_url": connection.websocket_url[:50] + "..." if connection.websocket_url else None,
            "callback_count": len(connection.update_callbacks),
        }
        
        # Add connectivity status from connection (stored from connectivity updates)
        if connection.connectivity_status:
            connectivity: ConnectivityStatus = connection.connectivity_status
            conn_data["connectivity_status"] = {
                "transport_connected": connectivity.transport_connected,
                "gateway_status": str(connectivity.gateway_status),
                "vessel_status": str(connectivity.vessel_status),
                "is_fully_connected": connectivity.is_fully_connected,
            }
        
        # Redact sensitive websocket URL
        if conn_data.get("websocket_url"):
            conn_data["websocket_url"] = "<REDACTED>"
        
        # Get gecko client info if available
        if connection.gecko_client:
            conn_data["gecko_client"] = _get_gecko_client_info(connection.gecko_client)
        
        connections[monitor_id] = conn_data
    
    return connections


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, config_entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    
    # Get coordinator and connection manager
    coordinator = hass.data.get(f"{DOMAIN}_coordinator_{config_entry.entry_id}")
    connection_manager = await async_get_connection_manager(hass)
    
    diagnostics_data = {
        "config_entry": {
            "entry_id": config_entry.entry_id,
            "title": config_entry.title,
            "domain": config_entry.domain,
            "state": config_entry.state.value,
        },
        "coordinator": _get_coordinator_diagnostics(coordinator),
        "connections": _get_connection_diagnostics(connection_manager),
        "runtime_data": {},
    }
    
    # Get runtime data (API client info)
    if hasattr(config_entry, "runtime_data") and config_entry.runtime_data:
        api_client = config_entry.runtime_data
        diagnostics_data["runtime_data"] = {
            "api_client_type": type(api_client).__name__,
        }
    
    return diagnostics_data