"""Support for Gecko light entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin
from . import GeckoConfigEntry

from gecko_iot_client.models.zone_types import ZoneType
from gecko_iot_client.models.lighting_zone import LightingZone


_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: GeckoConfigEntry,
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
        config_entry: GeckoConfigEntry,
        zone: LightingZone,
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
        
        # Determine color support based on zone capabilities
        self._supports_color = zone.rgbi is not None
        
        if self._supports_color:
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF
        
        # Initialize state and availability (will be set by async_added_to_hass event registration)
        self._attr_available = False
        self._update_state()

    def _get_zone_state(self) -> LightingZone | None:
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
        if zone is None:
            self._attr_is_on = None
            return
        
        self._attr_is_on = zone.active if zone.active is not None else False
        
        # Update color support dynamically (zone may gain color after initial setup)
        if zone.rgbi is not None and not self._supports_color:
            self._supports_color = True
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        
        # Update RGB color state
        if self._supports_color and zone.rgbi is not None:
            self._attr_rgb_color = (zone.rgbi.r, zone.rgbi.g, zone.rgbi.b)
            # Map intensity (0-255) to HA brightness (0-255)
            if zone.rgbi.i is not None:
                self._attr_brightness = zone.rgbi.i
            else:
                self._attr_brightness = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        # Availability is now updated via CONNECTIVITY_UPDATE events, not polling
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on, optionally with color/brightness/effect."""
        try:
            gecko_client = await self.coordinator.get_gecko_client()
            if not gecko_client:
                _LOGGER.error("No gecko client available for %s", self._attr_name)
                return
                
            light_zones = self.coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)
            zone: LightingZone | None = next(
                (z for z in light_zones if z.id == self._zone.id), None
            )
            if zone is None:
                _LOGGER.warning("Could not find lighting zone %s", self._zone.id)
                return
            
            # Handle color request (with optional brightness)
            if ATTR_RGB_COLOR in kwargs:
                r, g, b = kwargs[ATTR_RGB_COLOR]
                # Use brightness as intensity if provided, otherwise keep existing
                intensity = kwargs.get(ATTR_BRIGHTNESS)
                if intensity is None and zone.rgbi is not None:
                    intensity = zone.rgbi.i
                zone.set_color(r, g, b, intensity)
                return
            
            # Handle brightness-only change (keep current color)
            if ATTR_BRIGHTNESS in kwargs and zone.rgbi is not None:
                zone.set_color(
                    zone.rgbi.r,
                    zone.rgbi.g,
                    zone.rgbi.b,
                    kwargs[ATTR_BRIGHTNESS],
                )
                return
            
            # Simple on with no parameters
            zone.activate()
            
        except Exception as e:
            _LOGGER.error("Error turning on light %s: %s", self._attr_name, e)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        try:
            gecko_client = await self.coordinator.get_gecko_client()
            if not gecko_client:
                _LOGGER.error("No gecko client available for %s", self._attr_name)
                return
                
            light_zones = self.coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)
            zone: LightingZone | None = next(
                (z for z in light_zones if z.id == self._zone.id), None
            )
            if zone is None:
                _LOGGER.warning("Could not find lighting zone %s", self._zone.id)
                return
            
            zone.deactivate()
            
        except Exception as e:
            _LOGGER.error("Error turning off light %s: %s", self._attr_name, e)
