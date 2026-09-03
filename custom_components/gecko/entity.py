"""Base entity mixin for Gecko entities."""

from __future__ import annotations

import logging

from gecko_iot_client.models.events import EventChannel
from gecko_iot_client import GeckoIotClient
from homeassistant.core import HomeAssistant
from .coordinator import GeckoVesselCoordinator

_LOGGER = logging.getLogger(__name__)


class GeckoEntityAvailabilityMixin:
    """Mixin to provide availability checking for Gecko entities using gecko_iot_client events."""

    coordinator: GeckoVesselCoordinator
    hass: HomeAssistant
    _attr_available: bool
    _connectivity_callback_registered: bool = False
    _registered_gecko_client: GeckoIotClient | None = None

    @property
    def available(self) -> bool:
        """Return if entity is available.
        
        Override the cached property to make availability truly dynamic.
        Also re-registers connectivity callback if the gecko_client has been
        replaced (e.g., after reconnection).
        """
        self._ensure_callback_registered()
        return self._attr_available

    def _ensure_callback_registered(self) -> None:
        """Ensure the connectivity callback is registered on the current gecko_client.

        After reconnection, the connection manager replaces the gecko_client instance.
        This method detects that and re-registers the callback on the new client.
        """
        current_client = self._get_gecko_client_sync()
        if not current_client or current_client is self._registered_gecko_client:
            return

        # Client was replaced (reconnection happened) — re-register
        if self._registered_gecko_client:
            try:
                self._registered_gecko_client.off(
                    EventChannel.CONNECTIVITY_UPDATE, self._on_connectivity_update
                )
            except Exception:
                pass  # Old client may be disposed

        current_client.on(EventChannel.CONNECTIVITY_UPDATE, self._on_connectivity_update)
        self._registered_gecko_client = current_client
        self._connectivity_callback_registered = True
        _LOGGER.debug("Re-registered connectivity callback on new gecko_client")

    async def async_added_to_hass(self) -> None:
        """Register for connectivity updates when entity is added to hass."""
        await super().async_added_to_hass()  # type: ignore[misc]
        await self._manage_connectivity_callback(register=True)
        self._update_availability()

    async def async_will_remove_from_hass(self) -> None:
        """Unregister from connectivity updates when entity is removed."""
        await self._manage_connectivity_callback(register=False)
        await super().async_will_remove_from_hass()  # type: ignore[misc]

    async def _manage_connectivity_callback(self, register: bool) -> None:
        """Register or unregister connectivity event callback."""
        if register == self._connectivity_callback_registered:
            return

        # Register even while the transport is down. The connection manager keeps
        # the client instance, and this callback is what lets the entity recover
        # automatically when connectivity returns.
        gecko_client = self._get_gecko_client_sync()
        if not gecko_client:
            return

        if register:
            gecko_client.on(EventChannel.CONNECTIVITY_UPDATE, self._on_connectivity_update)
            self._registered_gecko_client = gecko_client
        else:
            gecko_client.off(EventChannel.CONNECTIVITY_UPDATE, self._on_connectivity_update)
            self._registered_gecko_client = None

        self._connectivity_callback_registered = register

    def _on_connectivity_update(self, connectivity_status) -> None:
        """Handle connectivity update events from gecko_iot_client.
        
        This callback is invoked from gecko_iot_client's background thread,
        so we must schedule the state update on the event loop.
        """
        new_availability = self._check_is_connected()
        if new_availability != self._attr_available:
            _LOGGER.debug(
                "Availability changed: %s -> %s (gateway=%s, vessel=%s)",
                self._attr_available,
                new_availability,
                connectivity_status.gateway_status,
                connectivity_status.vessel_status,
            )
            self._attr_available = new_availability
            # Use the proper thread-safe method to write state from background thread
            self.hass.loop.call_soon_threadsafe(
                self.async_write_ha_state
            )

    def _update_availability(self) -> None:
        """Update availability from the retained connection snapshot."""
        new_availability = self._check_is_connected()
        self._attr_available = new_availability

    def _check_is_connected(self) -> bool:
        """Check if the transport, gateway, and vessel are connected."""
        from .connection_manager import GECKO_CONNECTION_MANAGER_KEY

        connection_manager = self.hass.data.get(GECKO_CONNECTION_MANAGER_KEY)
        if not connection_manager:
            return False

        connection = connection_manager.get_connection(self.coordinator.monitor_id)
        return connection.is_fully_connected if connection else False

    def _get_gecko_client_sync(self) -> GeckoIotClient | None:
        """Get gecko client synchronously from connection manager."""
        from .connection_manager import GECKO_CONNECTION_MANAGER_KEY

        connection_manager = self.hass.data.get(GECKO_CONNECTION_MANAGER_KEY)
        if not connection_manager:
            return None

        connection = connection_manager._connections.get(self.coordinator.monitor_id)
        return connection.gecko_client if connection else None
