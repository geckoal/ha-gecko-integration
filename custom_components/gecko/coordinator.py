"""Data update coordinator for Gecko."""

from __future__ import annotations

import asyncio
import inspect
import logging
from datetime import timedelta
from typing import Any, Dict, List

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

# Import from geckoIotClient
from gecko_iot_client.models.zone_types import ZoneType, AbstractZone

from .const import DOMAIN
from .connection_manager import async_get_connection_manager, GeckoMonitorConnection

_LOGGER = logging.getLogger(__name__)

# Constants
UPDATE_INTERVAL_SECONDS = 30  # seconds between coordinator updates
MAX_CONSECUTIVE_FAILURES = 2  # max failures before attempting reconnect
RECONNECT_DELAY = 1  # seconds to wait before reconnecting
INITIAL_ZONE_TIMEOUT = 60.0  # seconds to wait for initial zone data


class GeckoVesselCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for a single Gecko vessel/spa following Home Assistant best practices."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        vessel_id: str,
        monitor_id: str,
        vessel_name: str,
    ) -> None:
        """Initialize the vessel coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{vessel_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.entry_id = entry_id
        self.vessel_id = vessel_id
        self.monitor_id = monitor_id
        self.vessel_name = vessel_name
        
        # Store zones for this vessel only (no monitor_id dictionary needed)
        self._zones: Dict[ZoneType, List[AbstractZone]] = {}
        
        # Store real-time state data for this vessel
        self._spa_state: Dict[str, Any] = {}
        
        # Track if this vessel has received initial zone data
        self._has_initial_zones = False
        
        # Event to signal when initial zone data is loaded
        self._initial_zones_loaded_event = asyncio.Event()
        
        # Callbacks for zone updates (for dynamic entity creation)
        self._zone_update_callbacks: list = []
        
        # Simple connection tracking
        self._consecutive_failures = 0

    def register_zone_update_callback(self, callback):
        """Register a callback to be called when zone data updates."""
        self._zone_update_callbacks.append(callback)

    async def _async_handle_zone_update(self, data: dict[str, Any]) -> None:
        """Handle zone update in the event loop."""
        # Trigger entity discovery when zones are updated
        self.async_set_updated_data(data)
        
        _LOGGER.debug("Zone data updated for vessel %s", self.vessel_name)

        # Call registered callbacks for dynamic entity creation
        for callback in self._zone_update_callbacks:
            try:
                if callable(callback) and callback is not None:
                    result = callback()
                    # If callback returns a coroutine, await it
                    if inspect.iscoroutine(result):
                        await result
            except Exception as ex:
                _LOGGER.error("Error in zone update callback for vessel %s: %s", self.vessel_name, ex, exc_info=True)

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from Gecko API."""
        try:
            # Check if connection exists and is active
            connection_manager = await async_get_connection_manager(self.hass)
            connection = connection_manager._connections.get(self.monitor_id)
            
            if not connection or not connection.is_connected:
                self._consecutive_failures += 1
                
                # After 2 consecutive failures (1 minute), try to reconnect with fresh token
                if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    _LOGGER.info("Connection lost for %s, attempting reconnect", self.vessel_name)
                    await self._simple_reconnect()
                    self._consecutive_failures = 0
            else:
                self._consecutive_failures = 0
            
            # Data will be updated by geckoIotClient callbacks
            return {"status": "active", "vessel_id": self.vessel_id}
        except Exception as exception:
            raise UpdateFailed(f"Error communicating with Gecko API for vessel {self.vessel_name}: {exception}") from exception

    def get_zones_by_type(self, zone_type: ZoneType) -> List[AbstractZone]:
        """Get zones of a specific type for this vessel (no monitor_id needed)."""
        zones = self._zones.get(zone_type, [])
        _LOGGER.debug("Retrieved %d zones of type %s for vessel %s", len(zones), zone_type, self.vessel_name)
        return zones

    def get_all_zones(self) -> Dict[ZoneType, List[AbstractZone]]:
        """Get all zones for this vessel."""
        return self._zones

    async def _simple_reconnect(self) -> None:
        """Simple reconnection with fresh token."""
        try:
            # Get config entry and API client
            entry = self.hass.config_entries.async_get_entry(self.entry_id)
            if not entry or not entry.runtime_data:
                return
            
            api_client = entry.runtime_data.api_client
            connection_manager = await async_get_connection_manager(self.hass)
            
            # Disconnect old connection
            await connection_manager.async_disconnect_monitor(self.monitor_id)
            await asyncio.sleep(RECONNECT_DELAY)
            
            # Get fresh token and reconnect
            livestream_data = await api_client.async_get_monitor_livestream(self.monitor_id)
            new_url = livestream_data.get("brokerUrl")
            
            if new_url:
                await self.async_setup_monitor_connection(new_url)
                _LOGGER.info("Reconnected %s", self.vessel_name)
                
        except Exception as e:
            _LOGGER.error("Failed to reconnect %s: %s", self.vessel_name, e)

    async def get_gecko_client(self):
        """Get the gecko client for this vessel's monitor."""
        try:
            connection_manager = await async_get_connection_manager(self.hass)
            connection = connection_manager._connections.get(self.monitor_id)
            
            if connection and connection.is_connected:
                return connection.gecko_client
            else:
                _LOGGER.warning("No active connection found for vessel %s (monitor %s)", self.vessel_name, self.monitor_id)
                return None
                
        except Exception as e:
            _LOGGER.error("Failed to get gecko client for vessel %s: %s", self.vessel_name, e)
            return None

    def _create_refresh_token_callback(self, websocket_url: str):
        """Create a refresh token callback for this vessel's monitor.
        
        This callback is invoked by the geckoIotClient when websocket tokens expire
        or are about to expire. It fetches a fresh websocket URL with new JWT tokens
        from the Gecko API using the OAuth2-managed access token.
        """
        def refresh_token_callback(monitor_id: str | None = None) -> str:
            """Handle token refresh by getting a new websocket URL.
            
            This is a synchronous callback invoked from background threads by the
            geckoIotClient library. We use run_coroutine_threadsafe to safely
            execute the async API call on Home Assistant's event loop.
            
            Args:
                monitor_id: The monitor ID that needs token refresh (optional, uses self.monitor_id if not provided)
                
            Returns:
                New websocket URL with fresh JWT token, or original URL on failure
            """
            # Use provided monitor_id or fall back to coordinator's monitor_id
            target_monitor_id = monitor_id or self.monitor_id
            _LOGGER.debug("Token refresh callback triggered for vessel %s (monitor %s)", self.vessel_name, target_monitor_id)
            
            try:
                # Get the config entry
                entry = self.hass.config_entries.async_get_entry(self.entry_id)
                if not entry:
                    _LOGGER.error("Config entry %s not found for vessel %s - cannot refresh token", self.entry_id, self.vessel_name)
                    return websocket_url
                
                # Get API client from runtime data
                if not hasattr(entry, 'runtime_data') or not entry.runtime_data:
                    _LOGGER.error("No runtime_data found for vessel %s - cannot refresh token", self.vessel_name)
                    return websocket_url
                
                api_client = entry.runtime_data.api_client
                if not api_client:
                    _LOGGER.error("No API client found for vessel %s - cannot refresh token", self.vessel_name)
                    return websocket_url
                
                # Fetch new livestream URL with fresh JWT token
                # This is a sync callback from background thread, so use run_coroutine_threadsafe
                _LOGGER.debug("Requesting new websocket URL for monitor %s", target_monitor_id)
                future = asyncio.run_coroutine_threadsafe(
                    api_client.async_get_monitor_livestream(target_monitor_id),
                    self.hass.loop
                )
                
                # Wait for the API call to complete (with timeout)
                livestream_data = future.result(timeout=30.0)
                
                # Extract the new websocket URL
                new_url = livestream_data.get("brokerUrl")
                if new_url:
                    _LOGGER.debug("Refreshed websocket URL for vessel %s (monitor %s)", self.vessel_name, target_monitor_id)
                    return new_url
                else:
                    _LOGGER.error("No brokerUrl in livestream response for vessel %s", self.vessel_name)
                    return websocket_url
                    
            except TimeoutError:
                _LOGGER.error("Timeout fetching new websocket URL for vessel %s - API call took too long", self.vessel_name)
                return websocket_url
            except Exception as e:
                _LOGGER.error("Failed to refresh token for vessel %s: %s", self.vessel_name, e, exc_info=True)
                return websocket_url
        
        return refresh_token_callback

    async def async_setup_monitor_connection(self, websocket_url: str) -> bool:
        """Set up a connection to this vessel's monitor using the singleton connection manager."""
        try:
            # Get the singleton connection manager
            connection_manager = await async_get_connection_manager(self.hass)
            
            # Create update callback for this vessel's coordinator
            def on_zone_update(updated_zones):
                _LOGGER.debug("Zone update received for vessel %s", self.vessel_name)
                
                # Store the updated zones from GeckoIotClient (these have state managers!)
                self._zones = updated_zones
                
                # Mark this vessel as having received zones
                if not self._has_initial_zones:
                    self._has_initial_zones = True
                    _LOGGER.debug("Initial zone data received for vessel %s", self.vessel_name)
                    if not self._initial_zones_loaded_event.is_set():
                        self._initial_zones_loaded_event.set()
                
                # Log which zones we received with state managers
                for zone_type, zones in updated_zones.items():
                    for zone in zones:
                        has_state_manager = hasattr(zone, '_state_manager') and zone._state_manager is not None
                        _LOGGER.debug("Vessel %s: Zone %s (type %s) has state manager: %s", 
                                    self.vessel_name, zone.id, zone_type.value, has_state_manager)
                
                # Schedule the async call to run on the event loop from background thread
                asyncio.run_coroutine_threadsafe(
                    self._async_handle_zone_update({"last_update": "zone_update"}),
                    self.hass.loop
                )
            
            # Create refresh token callback
            refresh_token_callback = self._create_refresh_token_callback(websocket_url)
                
            # Get or create connection with refresh token callback
            await connection_manager.async_get_or_create_connection(
                monitor_id=self.monitor_id,
                websocket_url=websocket_url,
                vessel_name=self.vessel_name,
                update_callback=on_zone_update,
                refresh_token_callback=refresh_token_callback,
            )
            
            _LOGGER.debug("Set up connection for vessel %s (monitor %s)", self.vessel_name, self.monitor_id)
            return True
            
        except Exception as e:
            _LOGGER.error("Failed to set up connection for vessel %s: %s", self.vessel_name, e, exc_info=True)
            return False

    async def async_get_operation_mode_status(self):
        """Get operation mode status for this vessel's monitor."""
        gecko_client = await self.get_gecko_client()
        if gecko_client:
            return gecko_client.operation_mode_status
        return None

    def update_spa_state(self, state_data: Dict[str, Any]) -> None:
        """Update spa state data and trigger coordinator update."""
        _LOGGER.debug("Updating spa state for vessel %s", self.vessel_name)
        self._spa_state = state_data
        
        # Schedule the async call to run on the event loop from background thread
        asyncio.run_coroutine_threadsafe(
            self._async_handle_zone_update({"last_update": state_data}),
            self.hass.loop
        )

    async def async_wait_for_initial_zone_data(self, timeout: float = INITIAL_ZONE_TIMEOUT) -> bool:
        """Wait for this vessel to receive its initial zone data."""
        try:
            await asyncio.wait_for(self._initial_zones_loaded_event.wait(), timeout=timeout)
            _LOGGER.debug("Initial zone data loaded for vessel %s within timeout", self.vessel_name)
            return True
        except asyncio.TimeoutError:
            _LOGGER.warning("Timeout waiting for initial zone data for vessel %s", self.vessel_name)
            return False

    def get_spa_state(self) -> Dict[str, Any] | None:
        """Get spa state data for this vessel."""
        return self._spa_state

    async def async_shutdown(self) -> None:
        """Shutdown coordinator and cleanup resources."""
        _LOGGER.debug("Shutting down coordinator for vessel %s (entry %s)", self.vessel_name, self.entry_id)
        
        try:
            # Connection manager will handle cleanup during Home Assistant shutdown
            # We don't disconnect here as the connection may be shared
            _LOGGER.debug("Coordinator releasing vessel %s (monitor %s)", self.vessel_name, self.monitor_id)
            
        except Exception as ex:
            _LOGGER.warning("Error during coordinator shutdown for vessel %s: %s", self.vessel_name, ex)
        
        self._zones.clear()
        self._spa_state.clear()
        self._zone_update_callbacks.clear()