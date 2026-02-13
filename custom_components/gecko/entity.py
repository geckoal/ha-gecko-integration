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

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Override the cached property to make availability truly dynamic.
        """
        return self._attr_available

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

        gecko_client = await self.coordinator.get_gecko_client()
        if not gecko_client:
            return

        if register:
            gecko_client.on(EventChannel.CONNECTIVITY_UPDATE, self._on_connectivity_update)
        else:
            gecko_client.off(EventChannel.CONNECTIVITY_UPDATE, self._on_connectivity_update)

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
        """Update availability from gecko_iot_client's is_connected property."""
        new_availability = self._check_is_connected()
        self._attr_available = new_availability

    def _check_is_connected(self) -> bool:
        """Check if gecko_iot_client is connected."""
        gecko_client = self._get_gecko_client_sync()
        if not gecko_client:
            return False

        return gecko_client.is_connected

    def _get_gecko_client_sync(self) -> GeckoIotClient | None:
        """Get gecko client synchronously from connection manager."""
        from .connection_manager import GECKO_CONNECTION_MANAGER_KEY

        connection_manager = self.hass.data.get(GECKO_CONNECTION_MANAGER_KEY)
        if not connection_manager:
            return None

        connection = connection_manager._connections.get(self.coordinator.monitor_id)
        return connection.gecko_client if connection else None
