"""Support for Gecko light entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.core import callback

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin
from . import GeckoConfigEntry

from gecko_iot_client.models.zone_types import ZoneType


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
    """Representation of a Gecko light.

    gecko_iot_client's LightingZone model already exposes RGB color and
    intensity (zone.rgbi, zone.set_color(r, g, b, i)); this entity now
    surfaces that as ColorMode.RGB instead of only ColorMode.ONOFF, mapping
    the Gecko intensity value "i" to Home Assistant's brightness (0-255).

    It also surfaces zone.effect / zone.set_effect(). The Gecko API only
    validates effect names by length (1-50 characters) - there is no fixed
    enum of valid names anywhere in the client library or in Gecko's public
    documentation. To avoid offering effect names that the device might not
    actually accept, effect_list is not hardcoded; instead it is built up
    from effect names actually reported by the device for this zone (e.g.
    ones set previously via the Gecko app or physical keypad), and grows as
    more are observed. The currently active effect is always included even
    before this discovery happens.
    """

    _attr_has_entity_name = True
    coordinator: GeckoVesselCoordinator

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        config_entry: GeckoConfigEntry,
        zone: Any,  # LightingZone from coordinator
    ) -> None:
        """Initialize the light."""
        super().__init__(coordinator)

        self._zone = zone
        self._attr_name = f"Light {zone.id}"
        self._attr_unique_id = f"{config_entry.entry_id}_{coordinator.vessel_id}_light_{zone.id}"

        # Device info for grouping entities - reference the actual device created in __init__.py
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )

        # Advertise color support only if this zone actually exposes rgbi.
        # Falls back cleanly to ON/OFF for zones that don't (e.g. older
        # firmware), matching the previous behavior for those zones.
        if self._zone_supports_color():
            self._attr_supported_color_modes = {ColorMode.RGB}
            self._attr_color_mode = ColorMode.RGB
        else:
            self._attr_supported_color_modes = {ColorMode.ONOFF}
            self._attr_color_mode = ColorMode.ONOFF

        # Advertise effect support only if this zone exposes an effect
        # attribute at all. See class docstring for why effect_list is
        # built up dynamically instead of hardcoded.
        self._attr_supported_features = LightEntityFeature(0)
        self._known_effects: set[str] = set()
        if self._zone_supports_effect():
            self._attr_supported_features |= LightEntityFeature.EFFECT

        # Initialize state and availability (will be set by async_added_to_hass event registration)
        self._attr_available = False
        self._update_state()

    def _zone_supports_color(self) -> bool:
        """Return True if this zone exposes an rgbi attribute at all."""
        return hasattr(self._zone, "rgbi")

    def _zone_supports_effect(self) -> bool:
        """Return True if this zone exposes an effect attribute at all."""
        return hasattr(self._zone, "effect")

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

            # Pull color/brightness from zone.rgbi when available.
            rgbi = getattr(zone, "rgbi", None)
            if rgbi is not None:
                self._attr_rgb_color = (rgbi.r, rgbi.g, rgbi.b)
                self._attr_brightness = rgbi.i if rgbi.i is not None else 255
            else:
                self._attr_rgb_color = None
                self._attr_brightness = None

            # Track and surface the current effect, growing effect_list
            # with any effect name we actually observe from the device.
            effect = getattr(zone, "effect", None)
            self._attr_effect = effect
            if effect:
                self._known_effects.add(effect)
            self._attr_effect_list = sorted(self._known_effects) if self._known_effects else None
        else:
            self._attr_is_on = None

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_state()
        # Availability is now updated via CONNECTIVITY_UPDATE events, not polling
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        """Turn the light on, optionally setting color and/or brightness."""
        try:
            # Check if gecko client is connected
            gecko_client = await self.coordinator.get_gecko_client()
            if not gecko_client:
                _LOGGER.error("No gecko client available for %s", self._attr_name)
                return

            # Get the light zone from coordinator and activate it
            light_zones = self.coordinator.get_zones_by_type(ZoneType.LIGHTING_ZONE)
            zone = next((z for z in light_zones if z.id == self._zone.id), None)
            if not zone:
                _LOGGER.warning("Could not find lighting zone %s", self._zone.id)
                return

            # Set effect when requested, before color handling: an effect
            # change is a distinct action from a plain color/brightness
            # change, and set_effect() already activates the zone.
            effect = kwargs.get("effect")
            set_effect_method = getattr(zone, "set_effect", None)
            if effect is not None and callable(set_effect_method):
                set_effect_method(effect)
                self._known_effects.add(effect)
                return

            # Set color/brightness when requested, or when this zone
            # supports color at all (so turning on preserves the last
            # known color instead of dropping back to a default).
            rgb_color = kwargs.get("rgb_color")
            brightness = kwargs.get("brightness")

            set_color_method = getattr(zone, "set_color", None)
            if callable(set_color_method) and (
                rgb_color is not None or brightness is not None or self._zone_supports_color()
            ):
                # Fill in missing values from current state, so e.g.
                # changing only brightness doesn't reset the color.
                current_rgbi = getattr(zone, "rgbi", None)
                r, g, b = rgb_color if rgb_color is not None else (
                    (current_rgbi.r, current_rgbi.g, current_rgbi.b) if current_rgbi else (255, 255, 255)
                )
                i = brightness if brightness is not None else (
                    current_rgbi.i if current_rgbi and current_rgbi.i is not None else 255
                )
                set_color_method(r=r, g=g, b=b, i=i)
                return

            activate_method = getattr(zone, "activate", None)
            if activate_method and callable(activate_method):
                activate_method()
            else:
                _LOGGER.warning("Zone %s does not have activate method", zone.id)
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
