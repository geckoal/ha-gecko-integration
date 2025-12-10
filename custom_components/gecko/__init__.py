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
    _LOGGER.info("Setting up Gecko integration with entry: %s", entry.entry_id)
    
    implementation = (
        await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    )
    _LOGGER.debug("OAuth2 implementation obtained: %s", implementation.domain)

    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)
    _LOGGER.debug("OAuth2 session created")

    # Create OAuth-based Gecko API client
    api_client = OAuthGeckoApi(hass, session)
    _LOGGER.debug("API client created")

    # Create one coordinator per vessel following Home Assistant best practices
    vessels = entry.data.get("vessels", [])
    vessels_count = len(vessels)
    _LOGGER.info("Creating %d vessel coordinators", vessels_count)
    _LOGGER.debug("Config entry data keys: %s", list(entry.data.keys()))
    if vessels_count == 0:
        _LOGGER.warning("No vessels in entry.data! Entry data: %s", {k: v for k, v in entry.data.items() if k != 'token'})
    
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
        _LOGGER.debug("Created coordinator for vessel %s (ID: %s, Monitor: %s)", vessel_name, vessel_id, monitor_id)
    
    # Store in runtime data
    entry.runtime_data = GeckoRuntimeData(
        api_client=api_client,
        coordinators=coordinators,
    )
    _LOGGER.info("Created %d vessel coordinators", len(coordinators))

    # Create devices for each vessel/spa and set up geckoIotClient
    _LOGGER.info("Setting up %d vessels and geckoIotClient connections", vessels_count)
    await _setup_vessels_and_gecko_clients(hass, entry)
    _LOGGER.info("Vessels and geckoIotClient setup completed")

    # Wait for initial zone data with a shorter timeout and proceed anyway if timeout
    _LOGGER.info("Waiting for initial zone data to be loaded for all monitors...")
    # Set up platforms immediately - entities will be created when zone data becomes available
    _LOGGER.info("Setting up platforms immediately")
    _LOGGER.debug("Forwarding setup to platforms: %s", _PLATFORMS)
    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
    _LOGGER.info("Platforms set up successfully")
    
    _LOGGER.info("Gecko integration setup completed successfully")

    return True


async def _setup_vessels_and_gecko_clients(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up devices for each vessel/spa and geckoIotClient connections."""
    _LOGGER.debug("_setup_vessels_and_gecko_clients called")
    
    runtime_data: GeckoRuntimeData = entry.runtime_data
    vessels = entry.data.get("vessels", [])
    _LOGGER.info("Found %d vessels in config entry data", len(vessels))
    
    if not vessels:
        _LOGGER.warning("No vessels found in config entry data!")
        return
    
    device_registry = dr.async_get(hass)
    api_client = runtime_data.api_client
    
    _LOGGER.debug("Setting up devices and geckoIotClient for %d vessels", len(vessels))
    
    # Match each vessel with its coordinator
    for i, (vessel, coordinator) in enumerate(zip(vessels, runtime_data.coordinators)):
        vessel_name = vessel.get("name", f"Vessel {i}")
        vessel_id = vessel.get("vesselId")
        monitor_id = vessel.get("monitorId")
        
        _LOGGER.info("Processing vessel %d: name=%s, vesselId=%s, monitorId=%s", 
                    i, vessel_name, vessel_id, monitor_id)
        
        try:
            _setup_vessel_device(entry, vessel, device_registry)
            _LOGGER.debug("Device setup completed for vessel %s", vessel_name)
            
            await _setup_vessel_gecko_client(vessel, api_client, coordinator)
            _LOGGER.debug("GeckoIotClient setup completed for vessel %s", vessel_name)
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
        metadata = spa_config.get("metadata", {})
        _LOGGER.info("Spa %s configuration: metadata=%s, zones=%s", vessel_name, metadata, list(zones.keys()))
        
        # Log details about available zones
        for zone_type, zone_data in zones.items():
            _LOGGER.debug("Spa %s - Zone type '%s': %s", vessel_name, zone_type, zone_data)
    else:
        _LOGGER.warning("No spa configuration found for vessel %s", vessel_name)
    
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
    
    _LOGGER.info("_setup_vessel_gecko_client called for vessel %s (ID: %s, Monitor: %s)", vessel_name, vessel_id, monitor_id)
    
    if not monitor_id:
        _LOGGER.error("No monitor ID found for vessel %s. Available keys: %s", vessel_name, list(vessel.keys()))
        return
    
    try:
        _LOGGER.info("Requesting livestream URL for monitor %s", monitor_id)
        livestream_data = await api_client.async_get_monitor_livestream(monitor_id)
        websocket_url = livestream_data.get("brokerUrl")
        
        if not websocket_url:
            _LOGGER.error("No WebSocket URL found in livestream response for monitor %s", monitor_id)
            return
            
        _LOGGER.info("Got WebSocket URL for monitor %s: %s", monitor_id, websocket_url[:50] + "...")
        
        # Don't create zones from spa configuration in coordinator - let GeckoIotClient handle this
        # The coordinator will get zones from the GeckoIotClient once it's connected and configured
        
        # Use the singleton connection manager through the coordinator
        success = await coordinator.async_setup_monitor_connection(
            websocket_url=websocket_url
        )
        
        if success:
            _LOGGER.info("✅ Successfully setup connection for monitor %s", monitor_id)
        else:
            raise ConnectionError(f"Failed to setup connection for monitor {monitor_id}")
            
    except Exception as ex:
        _LOGGER.error("Failed to set up connection for monitor %s: %s", monitor_id, ex, exc_info=True)
        raise


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    _LOGGER.info("Unloading Gecko integration entry: %s", entry.entry_id)
    
    # Clean up all vessel coordinators
    runtime_data: GeckoRuntimeData = entry.runtime_data
    for coordinator in runtime_data.coordinators:
        await coordinator.async_shutdown()
    _LOGGER.info("Shutdown %d vessel coordinators", len(runtime_data.coordinators))
    
    # Disconnect all monitors from the connection manager
    # This ensures fresh connections on reload with updated config/tokens
    try:
        connection_manager = await async_get_connection_manager(hass)
        vessels = entry.data.get("vessels", [])
        for vessel in vessels:
            monitor_id = vessel.get("monitorId")
            if monitor_id:
                _LOGGER.info("Disconnecting monitor %s during unload", monitor_id)
                await connection_manager.async_disconnect_monitor(monitor_id)
    except Exception as ex:
        _LOGGER.warning("Error disconnecting monitors during unload: %s", ex)
    
    return await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)
