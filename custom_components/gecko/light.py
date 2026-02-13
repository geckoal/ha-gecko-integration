"""Support for Gecko light entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.core import callback

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin

from gecko_iot_client.models.zone_types import ZoneType


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko light entities from a config entry."""

    # Get runtime data with per-vessel coordinators
    runtime_data = config_entry.runtime_data
    if not runtime_data or not runtime_data.coordinators:
        _LOGGER.error("No coordinators found in runtime_data for config entry %s", config_entry.entry_id)
        return

    # Track created entities to avoid duplicates
    created_entity_ids = set()

    # Create entity discovery function for each coordinator
    def create_discovery_callback(coordinator: GeckoVesselCoordinator):
        """Create a discovery callback for a specific coordinator."""
        def discover_new_light_entities():
            """Discover new light entities for new zones."""
            new_entities = []

            # Get light zones for this vessel's coordinator (no monitor_id needed)
            light_zones = coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)

            for zone in light_zones:
                # Check if entity already exists
                entity_id = f"{coordinator.vessel_name}_light_{zone.id}".lower()
                if entity_id not in created_entity_ids:
                    entity = GeckoLight(coordinator, config_entry, zone)
                    new_entities.append(entity)
                    created_entity_ids.add(entity_id)

            if new_entities:
                async_add_entities(new_entities)

        return discover_new_light_entities

    # Set up entities for each vessel coordinator
    for coordinator in runtime_data.coordinators:
        # Initial entity discovery for this coordinator
        discovery_callback = create_discovery_callback(coordinator)
        discovery_callback()

        # Register callback for dynamic entity creation
        coordinator.register_zone_update_callback(discovery_callback)


class GeckoLight(GeckoEntityAvailabilityMixin, CoordinatorEntity, LightEntity):
    """Representation of a Gecko light."""
    coordinator: GeckoVesselCoordinator

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        config_entry: ConfigEntry,
        zone: Any,  # LightingZone from coordinator
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)

        self._zone = zone
        self.entity_id = f"light.{coordinator.vessel_name}_light_{zone.id}".lower()

        self._attr_name = f"{coordinator.vessel_name} light zone {zone.id}"
        self._attr_unique_id = f"{config_entry.entry_id}_{coordinator.vessel_name}_light_{zone.id}"

        # Device info for grouping entities - reference the actual device created in __init__.py
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )

        # Set basic light features
        self._attr_supported_color_modes = {ColorMode.ONOFF}
        self._attr_color_mode = ColorMode.ONOFF

        # Initialize state and availability (will be set by async_added_to_hass event registration)
        self._attr_available = False
        self._update_state()

    def _get_zone_state(self) -> Any | None:
        """Get the current zone state from coordinator."""
        try:
            light_zones = self.coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)
            return next((z for z in light_zones if z.id == self._zone.id), None)
        except Exception as e:
            _LOGGER.warning("Error getting zone state for %s: %s", self._attr_name, e)
        return None

    def _update_state(self) -> None:
        """Update entity state from zone data."""
        zone = self._get_zone_state()
        if zone:
            self._attr_is_on = getattr(zone, 'active', False)
        else:
            self._attr_is_on = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        # Availability is now updated via CONNECTIVITY_UPDATE events, not polling
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on."""
        try:
            # Check if gecko client is connected
            gecko_client = await self.coordinator.get_gecko_client()
            if not gecko_client:
                _LOGGER.error("No gecko client available for %s", self._attr_name)
                return

            # Get the light zone from coordinator and activate it
            light_zones = self.coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)
            zone = next((z for z in light_zones if z.id == self._zone.id), None)
            if zone:
                activate_method = getattr(zone, "activate", None)
                if activate_method and callable(activate_method):
                    activate_method()
                else:
                    _LOGGER.warning("Zone %s does not have activate method", zone.id)
            else:
                _LOGGER.warning("Could not find lighting zone %s", self._zone.id)
        except Exception as e:
            _LOGGER.error("Error turning on light %s: %s", self._attr_name, e)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off."""
        try:
            # Check if gecko client is connected
            gecko_client = await self.coordinator.get_gecko_client()
            if not gecko_client:
                _LOGGER.error("No gecko client available for %s", self._attr_name)
                return

            # Get the light zone from coordinator and deactivate it
            light_zones = self.coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)
            zone = next((z for z in light_zones if z.id == self._zone.id), None)
            if zone:
                deactivate_method = getattr(zone, "deactivate", None)
                if deactivate_method and callable(deactivate_method):
                    deactivate_method()
                else:
                    _LOGGER.warning("Zone %s does not have deactivate method", zone.id)
            else:
                _LOGGER.warning("Could not find lighting zone %s", self._zone.id)
        except Exception as e:
            _LOGGER.error("Error turning off light %s: %s", self._attr_name, e)
