"""The Gecko integration."""

from __future__ import annotations
from dataclasses import dataclass
import logging
import sys
import os

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client, config_entry_oauth2_flow, config_validation as cv, device_registry as dr


from .api import OAuthGeckoApi
from .oauth_implementation import GeckoPKCEOAuth2Implementation
from .const import DOMAIN, OAUTH2_AUTHORIZE, OAUTH2_CLIENT_ID, OAUTH2_TOKEN
from .coordinator import GeckoVesselCoordinator
from .connection_manager import async_get_connection_manager

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Set up the Gecko component."""
    # Register hardcoded OAuth implementation with PKCE (no user credentials needed)
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        GeckoPKCEOAuth2Implementation(
            hass,
            DOMAIN,
            client_id=OAUTH2_CLIENT_ID,
            authorize_url=OAUTH2_AUTHORIZE,
            token_url=OAUTH2_TOKEN,
        ),
    )
    return True


@dataclass
class GeckoRuntimeData:
    """Runtime data for Gecko integration."""
    api_client: OAuthGeckoApi
    coordinators: list[GeckoVesselCoordinator]


# List the platforms that this integration supports.
_PLATFORMS: list[Platform] = [Platform.LIGHT, Platform.FAN, Platform.CLIMATE, Platform.SELECT, Platform.BINARY_SENSOR]  
_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Gecko from a config entry."""
    _LOGGER.debug("Setting up Gecko integration with entry: %s", entry.entry_id)
    
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    # Create OAuth-based Gecko API client
    api_client = OAuthGeckoApi(hass, session)

    # Create one coordinator per vessel following Home Assistant best practices
    vessels = entry.data.get("vessels", [])
    vessels_count = len(vessels)
    _LOGGER.debug("Creating %d vessel coordinators", vessels_count)
    if vessels_count == 0:
        _LOGGER.warning("No vessels found in config entry")
    
    coordinators = []
    for vessel in vessels:
        vessel_id = vessel.get("vesselId")
        monitor_id = vessel.get("monitorId")
        vessel_name = vessel.get("name", f"Vessel {vessel_id}")
        
        coordinator = GeckoVesselCoordinator(
            hass=hass,
            entry_id=entry.entry_id,
            vessel_id=vessel_id,
            monitor_id=monitor_id,
            vessel_name=vessel_name,
        )
        coordinators.append(coordinator)
    
    # Store in runtime data
    entry.runtime_data = GeckoRuntimeData(
        api_client=api_client,
        coordinators=coordinators,
    )
    _LOGGER.debug("Created %d vessel coordinators", len(coordinators))

    # Create devices for each vessel/spa and set up geckoIotClient
    await _setup_vessels_and_gecko_clients(hass, entry)

    # Set up platforms immediately - entities will be created when zone data becomes available
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    
    _LOGGER.info("Gecko integration setup completed for %d vessels", vessels_count)

    return True


async def _setup_vessels_and_gecko_clients(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up devices for each vessel/spa and geckoIotClient connections."""
    runtime_data: GeckoRuntimeData = entry.runtime_data
    vessels = entry.data.get("vessels", [])
    _LOGGER.debug("Setting up %d vessels", len(vessels))
    
    if not vessels:
        _LOGGER.warning("No vessels found in config entry data!")
        return
    
    device_registry = dr.async_get(hass)
    api_client = runtime_data.api_client
    
    _LOGGER.debug("Setting up devices and geckoIotClient for %d vessels", len(vessels))
    
    # Match each vessel with its coordinator
    for i, (vessel, coordinator) in enumerate(zip(vessels, runtime_data.coordinators)):
        vessel_name = vessel.get("name", f"Vessel {i}")
        monitor_id = vessel.get("monitorId")
        
        _LOGGER.debug("Setting up vessel: %s (monitor: %s)", vessel_name, monitor_id)
        
        try:
            _setup_vessel_device(entry, vessel, device_registry)
            await _setup_vessel_gecko_client(vessel, api_client, coordinator)
        except Exception as e:
            _LOGGER.error("Failed to setup vessel %s: %s", vessel_name, e, exc_info=True)


def _setup_vessel_device(entry: ConfigEntry, vessel: dict, device_registry: dr.DeviceRegistry) -> None:
    """Set up device registry entry for a vessel."""
    vessel_id = vessel.get("vesselId")
    vessel_name = vessel.get("name", f"Vessel {vessel_id}")
    vessel_type = vessel.get("type", "Unknown")
    protocol_name = vessel.get("protocolName", "Unknown")
    
    # Log spa configuration info for debugging
    spa_config = vessel.get("spa_configuration", {})
    if spa_config:
        zones = spa_config.get("zones", {})
        _LOGGER.debug("Spa %s has %d zone types configured", vessel_name, len(zones))
    else:
        _LOGGER.debug("No spa configuration found for vessel %s", vessel_name)
    
    # Create a more descriptive device name
    device_name = vessel_name
    
    # Create device entry for this spa/vessel
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(entry.domain, str(vessel_id))},
        name=device_name,
        manufacturer="Gecko",
        model=f"{vessel_type} ({protocol_name})",
        sw_version=None,
    )


async def _setup_vessel_gecko_client(vessel: dict, api_client: OAuthGeckoApi, coordinator: GeckoVesselCoordinator) -> None:
    """Set up geckoIotClient connection for a vessel using the singleton connection manager."""
    vessel_id = vessel.get("vesselId")
    vessel_name = vessel.get("name", f"Vessel {vessel_id}")
    monitor_id = vessel.get("monitorId")
    
    if not monitor_id:
        _LOGGER.error("No monitor ID found for vessel %s. Available keys: %s", vessel_name, list(vessel.keys()))
        return
    
    try:
        _LOGGER.debug("Requesting livestream URL for monitor %s", monitor_id)
        livestream_data = await api_client.async_get_monitor_livestream(monitor_id)
        websocket_url = livestream_data.get("brokerUrl")
        
        if not websocket_url:
            _LOGGER.error("No WebSocket URL found in livestream response for monitor %s", monitor_id)
            return
            
        _LOGGER.debug("Received WebSocket URL for monitor %s", monitor_id)
        
        # Don't create zones from spa configuration in coordinator - let GeckoIotClient handle this
        # The coordinator will get zones from the GeckoIotClient once it's connected and configured
        
        # Use the singleton connection manager through the coordinator
        success = await coordinator.async_setup_monitor_connection(
            websocket_url=websocket_url
        )
        
        if not success:
            raise ConnectionError(f"Failed to setup connection for monitor {monitor_id}")
            
    except Exception as ex:
        _LOGGER.error("Failed to set up connection for monitor %s: %s", monitor_id, ex, exc_info=True)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.debug("Unloading Gecko integration entry: %s", entry.entry_id)
    
    # Clean up all vessel coordinators
    runtime_data: GeckoRuntimeData = entry.runtime_data
    for coordinator in runtime_data.coordinators:
        await coordinator.async_shutdown()
    
    # Disconnect all monitors from the connection manager
    # This ensures fresh connections on reload with updated config/tokens
    try:
        connection_manager = await async_get_connection_manager(hass)
        vessels = entry.data.get("vessels", [])
        for vessel in vessels:
            monitor_id = vessel.get("monitorId")
            if monitor_id:
                _LOGGER.debug("Disconnecting monitor %s during unload", monitor_id)
                await connection_manager.async_disconnect_monitor(monitor_id)
    except Exception as ex:
        _LOGGER.error("Error disconnecting monitors during unload: %s", ex)
    
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
