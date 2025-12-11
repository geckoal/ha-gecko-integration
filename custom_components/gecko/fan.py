"""Support for Gecko fan entities (pumps with speed control)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin

from gecko_iot_client.models.zone_types import ZoneType, FlowZoneType
from gecko_iot_client.models.flow_zone import FlowZone, FlowZoneCapabilities

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko fan entities from a config entry."""
    runtime_data = config_entry.runtime_data
    if not runtime_data or not runtime_data.coordinators:
        _LOGGER.error("No coordinators found in runtime_data for config entry %s", config_entry.entry_id)
        return
    created_entity_ids = set()
    def create_discovery_callback(coordinator: GeckoVesselCoordinator):
        def discover_new_fan_entities():
            new_entities = []
            vessel_coordinator: GeckoVesselCoordinator = coordinator
            pump_zones = vessel_coordinator.get_zones_by_type(ZoneType.FLOW_ZONE)
            flow_zones = [zone for zone in pump_zones if isinstance(zone, FlowZone)]
            for zone in flow_zones:
                entity_id = f"{vessel_coordinator.vessel_name}_pump_{zone.id}"
                if entity_id not in created_entity_ids:
                    entity = GeckoFan(vessel_coordinator, config_entry, zone)
                    new_entities.append(entity)
                    created_entity_ids.add(entity_id)
                    _LOGGER.debug("Created fan entity for vessel %s, zone %s", vessel_coordinator.vessel_name, zone.id)
            if new_entities:
                async_add_entities(new_entities)
        return discover_new_fan_entities
    for coordinator in runtime_data.coordinators:
        discovery_callback = create_discovery_callback(coordinator)
        discovery_callback()
        coordinator.register_zone_update_callback(discovery_callback)

class GeckoFan(GeckoEntityAvailabilityMixin, CoordinatorEntity, FanEntity):
    """Representation of a Gecko pump fan (multi-speed or variable speed)."""
    coordinator: GeckoVesselCoordinator
    
    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        config_entry: ConfigEntry,
        zone: FlowZone,  # FlowZone from coordinator
    ) -> None:
        """Initialize the Pump Fan."""
        FanEntity.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._coordinator: GeckoVesselCoordinator = coordinator
        self._zone = zone
        self.entity_id = f"fan.{coordinator.vessel_name}_pump_{zone.id}"
        self._attr_name = f"{coordinator.vessel_name} {zone.name}"
        self._attr_unique_id = f"{config_entry.entry_id}_{coordinator.vessel_name}_pump_{zone.id}"

        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_supported_features = (
            FanEntityFeature.TURN_OFF | FanEntityFeature.TURN_ON 
        )
        
        if FlowZoneCapabilities.SUPPORTS_SPEED_PRESETS in self._zone.capabilities:
            self._attr_supported_features |= FanEntityFeature.SET_SPEED
            
            self._speed_list = [preset.name for preset in self._zone.presets]
        
            self._attr_speed_list = self._speed_list
        
        # Set icon based on zone type
        self._attr_icon = self._get_icon_for_zone_type()
        
        # Initialize state and availability from zone (will be set by async_added_to_hass event registration)
        self._attr_available = False
        self._update_from_zone()
 
        
    def _get_icon_for_zone_type(self) -> str:
        """Return icon based on flow zone type."""
        zone_type = self._zone.type
        if zone_type == FlowZoneType.WATERFALL_ZONE:
            return "mdi:waterfall"
        elif zone_type == FlowZoneType.BLOWER_ZONE:
            return "mdi:wind-power"
        else:  # FLOW_ZONE (pump)
            return "mdi:pump"
    
    async def async_added_to_hass(self) -> None:
        """Register update callback when entity is added to hass."""
        await super().async_added_to_hass()
        self.coordinator.async_add_listener(self._handle_coordinator_update)
        
  
    def _update_from_zone(self) -> None:
        """Update state attributes from zone data."""
        self._attr_is_on = self._zone.active
        self._attr_percentage = int(self._zone.speed) if self._zone.speed is not None else 0
        
        if isinstance(self._zone.speed, (int, float)):
            if self._zone.speed < 34:
                self._attr_speed = "low"
            elif self._zone.speed < 67:
                self._attr_speed = "medium"
            elif self._zone.speed <= 100:
                self._attr_speed = "high"
        
        if not self._zone.active:
            self._attr_speed = "off"
            self._attr_is_on = False
    
    @callback
    def _handle_coordinator_update(self) -> None:
        _LOGGER.debug("Updating fan %s: is_on=%s, speed=%s", self._attr_name, self._attr_is_on, self._attr_speed)
        self._update_from_zone()
        self.async_write_ha_state()
        
    async def async_turn_on(self, percentage: int | None = None, preset_mode: str | None = None, **kwargs) -> None:
        """Turn the fan on. Optionally set speed by percentage."""
        _LOGGER.debug("Turning on pump %s", self._attr_name)
        # Map percentage to speed
        speed = "low"
        if percentage is not None:
            if percentage < 34:
                speed = "low"
            elif percentage < 67:
                speed = "medium"
            else:
                speed = "high"
        await self.async_set_speed(speed)
        
    async def async_turn_off(self, **kwargs) -> None:
        """Turn the fan off."""
        _LOGGER.debug("Turning off pump %s", self._attr_name)
    
        self._zone.deactivate()
        
    @property
    def is_on(self) -> bool | None:
        """Return true if the entity is on."""
        return self._attr_is_on 
        
    async def async_set_speed(self, speed: str) -> None:
        _LOGGER.debug("Setting pump %s speed to %s", self._attr_name, speed)
        # Map string speed to integer value expected by Gecko API
        speed_map = {
            "off": 0,
            "low": 1,
            "medium": 2,
            "high": 3,
        }
        speed_value = speed_map.get(speed, 0)
        try:
            gecko_client = await self._coordinator.get_gecko_client()
            if not gecko_client:
                _LOGGER.error("No gecko client available for %s", self._attr_name)
                return
            pump_zones = self._coordinator.get_zones_by_type(ZoneType.FLOW_ZONE)
            zone = next((z for z in pump_zones if z.id == self._zone.id), None)
            if zone:
                set_speed_method = getattr(zone, "set_speed", None)
                if set_speed_method and callable(set_speed_method):
                    set_speed_method(speed_value)
                    # Let the coordinator update handle state changes
                    _LOGGER.debug("Sent speed command for pump %s to %s", self._attr_name, speed)
                else:
                    _LOGGER.warning("Zone %s does not have set_speed method", zone.id)
            else:
                _LOGGER.warning("Could not find pump zone %s", self._zone.id)
        except Exception as e:
            _LOGGER.error("Error setting pump %s speed: %s", self._attr_name, e)
