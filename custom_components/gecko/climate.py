"""Support for Gecko climate entities (temperature control)."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin
from gecko_iot_client.models.zone_types import ZoneType
from gecko_iot_client.models.temperature_control_zone import TemperatureControlZone

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko climate entities from a config entry."""
    coordinators = config_entry.runtime_data.coordinators

    # Track which zones have already been added for each coordinator
    added_zones: dict[str, set[int]] = {}

    @callback
    def discover_new_climate_entities(coordinator: GeckoVesselCoordinator) -> None:
        """Discover climate entities for temperature control zones."""
        zones = coordinator.get_zones_by_type(ZoneType.TEMPERATURE_CONTROL_ZONE)

        # Get or create the set of added zone IDs for this coordinator
        vessel_key = f"{coordinator.entry_id}_{coordinator.vessel_id}"
        if vessel_key not in added_zones:
            added_zones[vessel_key] = set()

        entities = []
        for zone in zones:
            if not hasattr(zone, "id"):
                _LOGGER.warning("Zone object missing 'id' attribute: %s", zone)
                continue

            # Only add if not already added
            if zone.id not in added_zones[vessel_key]:
                entities.append(GeckoClimate(coordinator, zone))
                added_zones[vessel_key].add(zone.id)

        if entities:
            async_add_entities(entities)
            _LOGGER.debug(
                "Added %d climate entities for vessel %s",
                len(entities),
                coordinator.vessel_name,
            )
        else:
            _LOGGER.debug("No new climate entities to add for vessel %s", coordinator.vessel_name)

    # Set up initial entities and register for updates
    for coordinator in coordinators:
        discover_new_climate_entities(coordinator)
        coordinator.register_zone_update_callback(
            lambda coord=coordinator: discover_new_climate_entities(coord)
        )


class GeckoClimate(GeckoEntityAvailabilityMixin, CoordinatorEntity[GeckoVesselCoordinator], ClimateEntity):
    """Representation of a Gecko climate control."""

    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    _attr_hvac_modes = [HVACMode.HEAT]
    _attr_hvac_mode = HVACMode.HEAT
    _attr_target_temperature_step = 0.5

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        zone: TemperatureControlZone,
    ) -> None:
        """Initialize the climate control."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = f"{coordinator.entry_id}_{coordinator.vessel_id}_climate_{zone.id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))}
        )
        self._attr_name = f"Thermostat {zone.id}"

        # Set temperature limits
        self._attr_min_temp = self._zone.min_temperature_set_point_c
        self._attr_max_temp = self._zone.max_temperature_set_point_c

        # Initialize availability (will be set by async_added_to_hass event registration)
        self._attr_available = False

        # Initialize state from zone
        self._update_from_zone()

    def _update_from_zone(self) -> None:
        """Update state attributes from zone data."""
        if self._zone.status:
            self._attr_hvac_action = (
                HVACAction.HEATING if self._zone.status.is_heating else HVACAction.IDLE
            )
        else:
            self._attr_hvac_action = HVACAction.IDLE

        self._attr_current_temperature = self._zone.temperature
        self._attr_target_temperature = self._zone.target_temperature
        self._attr_max_temp = self._zone.max_temperature_set_point_c
        self._attr_min_temp = self._zone.min_temperature_set_point_c

        _LOGGER.debug(
            "Zone %s: current=%s°C, target=%s°C",
            self._zone.id,
            self._attr_current_temperature,
            self._attr_target_temperature,
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        _LOGGER.debug("Updating climate entity %s", self.entity_id)
        self._update_from_zone()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get("temperature")) is None:
            return

        try:
            # set_target_temperature is a synchronous method, run in executor
            await self.hass.async_add_executor_job(
                self._zone.set_target_temperature, temperature
            )

            _LOGGER.debug(
                "Set target temperature to %.1f°C for %s",
                temperature,
                self.entity_id,
            )
        except Exception as err:
            _LOGGER.error(
                "Error setting temperature for %s: %s", self.entity_id, err
            )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode != HVACMode.HEAT:
            raise ServiceValidationError(
                f"Unsupported HVAC mode: {hvac_mode}. Only HEAT mode is supported.",
                translation_domain=DOMAIN,
                translation_key="unsupported_hvac_mode",
            )

        # HEAT mode is the only supported mode and is always active
        _LOGGER.debug("HVAC mode set to HEAT for %s (no action required)", self.entity_id)
