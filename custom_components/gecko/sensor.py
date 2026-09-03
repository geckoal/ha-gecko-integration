"""Sensor entities for Gecko telemetry."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from gecko_iot_client.models.flow_zone import FlowZone
from gecko_iot_client.models.temperature_control_zone import (
    TemperatureControlZone,
    TemperatureControlZoneStatus,
)
from gecko_iot_client.models.zone_types import ZoneType

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin
from .telemetry import (
    FLOW_SPEED_MODE_OPTIONS,
    derive_flow_speed_mode,
    get_flow_initiators,
    get_flow_manual_demand_reason,
    get_flow_runtime_state,
    get_flow_speed_step_values,
    get_supported_flow_speed_modes,
    get_temperature_flow_status,
    is_manual_flow_demand,
)

_LOGGER = logging.getLogger(__name__)

TEMPERATURE_STATUS_OPTIONS: tuple[str, ...] = tuple(
    status.name for status in TemperatureControlZoneStatus
)

DEVICE_TELEMETRY_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="rf_signal_strength",
        name="RF Signal Strength",
        translation_key="rf_signal_strength",
        icon="mdi:signal",
    ),
    SensorEntityDescription(
        key="rf_channel",
        name="RF Channel",
        translation_key="rf_channel",
        icon="mdi:radio-tower",
    ),
    SensorEntityDescription(
        key="pack_configuration",
        name="Pack Configuration",
        translation_key="pack_configuration",
        icon="mdi:package-variant-closed",
    ),
    SensorEntityDescription(
        key="home_firmware_version",
        name="Home (EN) Firmware Version",
        translation_key="home_firmware_version",
        icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="home_serial_number",
        name="Home (EN) Serial Number",
        translation_key="home_serial_number",
        icon="mdi:identifier",
    ),
    SensorEntityDescription(
        key="spa_firmware_version",
        name="Spa (CO) Firmware Version",
        translation_key="spa_firmware_version",
        icon="mdi:chip",
    ),
    SensorEntityDescription(
        key="spa_serial_number",
        name="Spa (CO) Serial Number",
        translation_key="spa_serial_number",
        icon="mdi:identifier",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko sensor entities from a config entry."""
    runtime_data = config_entry.runtime_data
    if not runtime_data or not runtime_data.coordinators:
        _LOGGER.error(
            "No coordinators found in runtime_data for config entry %s",
            config_entry.entry_id,
        )
        return

    added_temperature_zone_ids: dict[str, set[str]] = {}
    added_flow_zone_ids: dict[str, set[str]] = {}

    @callback
    def discover_new_sensor_entities(coordinator: GeckoVesselCoordinator) -> None:
        """Discover telemetry sensors for temperature and flow zones."""
        vessel_key = f"{coordinator.entry_id}_{coordinator.vessel_id}"
        added_temperature_zone_ids.setdefault(vessel_key, set())
        added_flow_zone_ids.setdefault(vessel_key, set())

        new_entities: list[SensorEntity] = []

        for zone in coordinator.get_zones_by_type(ZoneType.TEMPERATURE_CONTROL_ZONE):
            zone_id = str(zone.id)
            if zone_id in added_temperature_zone_ids[vessel_key]:
                continue

            if not isinstance(zone, TemperatureControlZone):
                continue

            new_entities.extend(
                (
                    GeckoTemperatureStatusSensor(coordinator, zone),
                    GeckoTemperatureFlowStatusSensor(coordinator, zone),
                )
            )
            added_temperature_zone_ids[vessel_key].add(zone_id)

        for zone in coordinator.get_zones_by_type(ZoneType.FLOW_ZONE):
            zone_id = str(zone.id)
            if zone_id in added_flow_zone_ids[vessel_key]:
                continue

            if not isinstance(zone, FlowZone):
                continue

            new_entities.append(GeckoFlowSpeedModeSensor(coordinator, zone))
            added_flow_zone_ids[vessel_key].add(zone_id)

        if new_entities:
            async_add_entities(new_entities)

    for coordinator in runtime_data.coordinators:
        async_add_entities(
            [
                *(
                    GeckoDeviceTelemetrySensor(coordinator, description)
                    for description in DEVICE_TELEMETRY_SENSOR_DESCRIPTIONS
                ),
                GeckoRawApiSensor(coordinator),
            ]
        )
        discover_new_sensor_entities(coordinator)
        coordinator.register_zone_update_callback(
            lambda coord=coordinator: discover_new_sensor_entities(coord)
        )


class GeckoDeviceTelemetrySensor(
    GeckoEntityAvailabilityMixin,
    CoordinatorEntity[GeckoVesselCoordinator],
    SensorEntity,
):
    """Expose RF and EN/CO metadata from the raw Gecko shadow."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize a device telemetry sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.entry_id}_{coordinator.vessel_id}_{description.key}"
        )
        self._attr_suggested_object_id = slugify(
            f"{coordinator.vessel_name}_{description.key}"
        )
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_available = False
        self._update_from_coordinator()

    def _update_from_coordinator(self) -> None:
        """Update this sensor from retained device telemetry."""
        self._attr_native_value = self.coordinator.get_device_telemetry().get(
            self.entity_description.key
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated shadow data."""
        self._update_from_coordinator()
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, str | tuple[str, ...]]:
        """Show which raw API field supplied the telemetry value."""
        attributes: dict[str, str | tuple[str, ...]] = {}
        source = self.coordinator.get_device_telemetry_source(
            self.entity_description.key
        )
        if source:
            attributes["source_field"] = source
        elif candidate_paths := (
            self.coordinator.get_device_metadata_candidate_paths()
        ):
            attributes["candidate_fields"] = candidate_paths
        return attributes


class GeckoRawApiSensor(
    GeckoEntityAvailabilityMixin,
    CoordinatorEntity[GeckoVesselCoordinator],
    SensorEntity,
):
    """Expose credential-redacted raw API payloads for opt-in debugging."""

    _attr_has_entity_name = True
    _attr_name = "Raw API Data"
    _attr_icon = "mdi:code-json"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: GeckoVesselCoordinator) -> None:
        """Initialize the raw API diagnostic sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = (
            f"{coordinator.entry_id}_{coordinator.vessel_id}_raw_api_data"
        )
        self._attr_suggested_object_id = slugify(
            f"{coordinator.vessel_name}_raw_api_data"
        )
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_available = False
        self._update_from_coordinator()

    def _update_from_coordinator(self) -> None:
        """Update the diagnostic state from captured API payloads."""
        self._attr_native_value = (
            "captured" if self.coordinator.get_raw_api_payloads() else "empty"
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the latest redacted payload grouped by source."""
        return {"payloads": self.coordinator.get_raw_api_payloads()}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle newly captured API data."""
        self._update_from_coordinator()
        self.async_write_ha_state()


class GeckoTemperatureStatusSensor(
    GeckoEntityAvailabilityMixin,
    CoordinatorEntity[GeckoVesselCoordinator],
    SensorEntity,
):
    """Expose the full Gecko temperature-control status enum."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TEMPERATURE_STATUS_OPTIONS

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        zone: TemperatureControlZone,
    ) -> None:
        """Initialize the temperature status sensor."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = (
            f"{coordinator.entry_id}_{coordinator.vessel_id}_temperature_status_{zone.id}"
        )
        self._attr_name = f"{zone.name} Status"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_available = False
        self._update_from_zone()

    def _update_from_zone(self) -> None:
        """Update sensor state from the Gecko temperature zone."""
        self._attr_native_value = self._zone.status.name if self._zone.status else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return additional temperature telemetry."""
        return {
            "status_code": self._zone.status.value if self._zone.status else None,
            "eco_mode": self._zone.mode.eco if self._zone.mode else None,
            "current_temperature": self._zone.temperature,
            "target_temperature": self._zone.target_temperature,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_zone()
        self.async_write_ha_state()


class GeckoTemperatureFlowStatusSensor(
    GeckoEntityAvailabilityMixin,
    CoordinatorEntity[GeckoVesselCoordinator],
    SensorEntity,
):
    """Expose the raw Gecko flow status for a temperature-control zone."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:waves-arrow-right"

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        zone: TemperatureControlZone,
    ) -> None:
        """Initialize a temperature-zone flow status sensor."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = (
            f"{coordinator.entry_id}_{coordinator.vessel_id}_flow_status_{zone.id}"
        )
        self._attr_name = f"{zone.name} Flow Status"
        self._attr_suggested_object_id = slugify(
            f"{coordinator.vessel_name}_{zone.name}_flow_status"
        )
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_available = False
        self._update_from_coordinator()

    def _update_from_coordinator(self) -> None:
        """Update the raw flow status from the latest shadow."""
        self._attr_native_value = get_temperature_flow_status(
            self._zone,
            self.coordinator.get_spa_state(),
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Return the raw shadow field used for this zone."""
        return {
            "source_field": (
                "MQTT shadow: state.reported.zones.temperatureControl."
                f"{self._zone.id}.flo_"
            )
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated flow status data."""
        self._update_from_coordinator()
        self.async_write_ha_state()


class GeckoFlowSpeedModeSensor(
    GeckoEntityAvailabilityMixin,
    CoordinatorEntity[GeckoVesselCoordinator],
    SensorEntity,
):
    """Expose the derived Gecko flow speed mode."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = FLOW_SPEED_MODE_OPTIONS

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        zone: FlowZone,
    ) -> None:
        """Initialize the flow speed mode sensor."""
        super().__init__(coordinator)
        self._zone = zone
        self._attr_unique_id = (
            f"{coordinator.entry_id}_{coordinator.vessel_id}_flow_speed_mode_{zone.id}"
        )
        self._attr_name = f"{zone.name} Speed Mode"
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_available = False
        self._update_from_zone()

    def _update_from_zone(self) -> None:
        """Update sensor state from the Gecko flow zone."""
        self._attr_native_value = derive_flow_speed_mode(self._zone)

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return additional flow telemetry."""
        spa_state = self.coordinator.get_spa_state()
        temperature_zones = self.coordinator.get_zones_by_type(
            ZoneType.TEMPERATURE_CONTROL_ZONE
        )
        return {
            "active": self._zone.active,
            "raw_speed": self._zone.speed,
            "zone_type": self._zone.type.value,
            "manual_demand": is_manual_flow_demand(
                self._zone,
                spa_state,
                temperature_zones,
            ),
            "manual_demand_reason": get_flow_manual_demand_reason(
                self._zone,
                spa_state,
                temperature_zones,
            ),
            "initiators": sorted(get_flow_initiators(self._zone, spa_state)),
            "raw_zone_state": get_flow_runtime_state(self._zone, spa_state),
            "speed_steps": list(get_flow_speed_step_values(self._zone)),
            "supported_speed_modes": list(get_supported_flow_speed_modes(self._zone)),
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        self._update_from_zone()
        self.async_write_ha_state()
