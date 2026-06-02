"""Support for Gecko fan entities (pumps with speed control)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin
from . import GeckoConfigEntry

from gecko_iot_client.models.zone_types import ZoneType, FlowZoneType
from gecko_iot_client.models.flow_zone import (
    FlowZone,
    FlowZoneCapabilities,
    PumpSpeedType,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: GeckoConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko fan entities from a config entry."""
    runtime_data = config_entry.runtime_data
    if not runtime_data or not runtime_data.coordinators:
        _LOGGER.error(
            "No coordinators found in runtime_data for config entry %s",
            config_entry.entry_id,
        )
        return

    created_entity_ids: set[str] = set()

    def create_discovery_callback(coordinator: GeckoVesselCoordinator):
        def discover_new_fan_entities():
            new_entities = []
            pump_zones = coordinator.get_zones_by_type(ZoneType.FLOW_ZONE)
            flow_zones = [zone for zone in pump_zones if isinstance(zone, FlowZone)]

            for zone in flow_zones:
                entity_id = f"{coordinator.vessel_name}_pump_{zone.id}".lower()
                if entity_id not in created_entity_ids:
                    entity = GeckoFan(coordinator, config_entry, zone)
                    new_entities.append(entity)
                    created_entity_ids.add(entity_id)
                    _LOGGER.debug(
                        "Created fan entity for vessel %s, zone %s (type: %s)",
                        coordinator.vessel_name,
                        zone.id,
                        zone.pump_speed_type.value,
                    )

            if new_entities:
                async_add_entities(new_entities)

        return discover_new_fan_entities

    for coordinator in runtime_data.coordinators:
        discovery_callback = create_discovery_callback(coordinator)
        discovery_callback()
        coordinator.register_zone_update_callback(discovery_callback)


class GeckoFan(GeckoEntityAvailabilityMixin, CoordinatorEntity, FanEntity):
    """Representation of a Gecko pump fan (multi-speed or variable speed)."""

    _attr_has_entity_name = True

    coordinator: GeckoVesselCoordinator

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        config_entry: GeckoConfigEntry,
        zone: FlowZone,

    ) -> None:
        """Initialize the Pump Fan."""
        FanEntity.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._coordinator: GeckoVesselCoordinator = coordinator
        self._zone = zone
        self._attr_name = zone.name
        self._attr_unique_id = (
            f"{config_entry.entry_id}_{coordinator.vessel_id}_pump_{zone.id}"

        )

        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )

        # Determine features based on pump speed type
        self._pump_speed_type = zone.pump_speed_type
        self._attr_supported_features = (
            FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON
        )

        if self._pump_speed_type != PumpSpeedType.SINGLE_SPEED:
            self._attr_supported_features |= FanEntityFeature.SET_SPEED

        # For multi-speed pumps, set the speed_count so HA knows how many
        # discrete steps to show. For variable speed, use the actual count.
        if self._pump_speed_type == PumpSpeedType.TWO_SPEED:
            self._attr_speed_count = 2
        elif self._pump_speed_type == PumpSpeedType.VARIABLE_SPEED:
            self._attr_speed_count = zone.speed_count

        # Set icon based on zone type
        self._attr_icon = self._get_icon_for_zone_type()

        # Initialize state
        self._attr_available = False
        self._update_from_zone()

    def _get_icon_for_zone_type(self) -> str:
        """Return icon based on flow zone type."""
        zone_type = self._zone.type
        if zone_type == FlowZoneType.WATERFALL_ZONE:
            return "mdi:waterfall"
        elif zone_type == FlowZoneType.BLOWER_ZONE:
            return "mdi:wind-power"
        else:
            return "mdi:pump"

    async def async_added_to_hass(self) -> None:
        """Register update callback when entity is added to hass."""
        await super().async_added_to_hass()
        self.coordinator.async_add_listener(self._handle_coordinator_update)

    def _update_from_zone(self) -> None:
        """Update state attributes from zone data."""
        self._attr_is_on = self._zone.active or False

        if not self._attr_is_on:
            self._attr_percentage = 0
        elif self._pump_speed_type == PumpSpeedType.SINGLE_SPEED:
            self._attr_percentage = 100
        else:
            # Use the zone's conversion to get the HA percentage
            self._attr_percentage = self._zone.speed_to_percentage(
                self._zone.speed or 0
            )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator data update."""
        _LOGGER.debug(
            "Updating fan %s: is_on=%s, percentage=%s",
            self._attr_name,
            self._attr_is_on,
            self._attr_percentage,
        )
        self._update_from_zone()
        self.async_write_ha_state()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally at a specific speed percentage."""
        if percentage is not None:
            await self.async_set_percentage(percentage)
        else:
            # Turn on at full speed (or last speed for variable)
            self._zone.activate()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        self._zone.deactivate()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the speed percentage.

        HA sends 0 for off, or a value 1-100 split evenly across speed_count steps.
        We convert that to the actual device speed value via the zone's converter.
        """
        if percentage == 0:
            self._zone.deactivate()
            return

        self._zone.set_speed_by_percentage(percentage)

    @property
    def is_on(self) -> bool | None:
        """Return true if the entity is on."""
        return self._attr_is_on
