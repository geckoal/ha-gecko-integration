"""Sensors for Gecko: extension shadow metrics (e.g. Waterlab) and connectivity."""

from __future__ import annotations

import logging
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin
from .shadow_metrics import infer_sensor_metadata, metric_path_to_entity_slug

_LOGGER = logging.getLogger(__name__)


def _metric_enabled_by_default(path: str) -> bool:
    """Expose likely water-chemistry metrics by default; other shadow leaves stay disabled."""
    lower = path.lower()
    return any(
        k in lower
        for k in (
            "ph",
            "orp",
            "chlorine",
            "bromine",
            "salinity",
            "tds",
            "waterlab",
            "sanitizer",
            "oxidation",
        )
    )


def _humanize_metric_name(path: str) -> str:
    """Short display name from dotted path."""
    tail = path.split(".")[-1]
    return tail.replace("_", " ").strip().title() or path


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko sensor entities."""
    if not hasattr(config_entry, "runtime_data") or not config_entry.runtime_data:
        _LOGGER.error("No runtime_data found for config entry")
        return

    coordinators = config_entry.runtime_data.coordinators
    if not coordinators:
        _LOGGER.warning("No vessel coordinators found")
        return

    initial_entities: list[SensorEntity] = []

    for coordinator in coordinators:
        await coordinator.async_wait_for_initial_zone_data(timeout=30.0)
        client = await coordinator.get_gecko_client()
        if client:
            coordinator.sync_refresh_shadow_metrics(client)
        pending = coordinator.take_pending_new_metric_paths()
        if pending:
            initial_entities.extend(
                GeckoShadowMetricSensor(coordinator, config_entry, p)
                for p in pending
            )

        @callback
        def _on_coordinator_listener(
            coord: GeckoVesselCoordinator = coordinator,
        ) -> None:
            added = coord.take_pending_new_metric_paths()
            if not added:
                return
            async_add_entities(
                [
                    GeckoShadowMetricSensor(coord, config_entry, path)
                    for path in added
                ]
            )

        config_entry.async_on_unload(
            coordinator.async_add_listener(_on_coordinator_listener)
        )

    if initial_entities:
        async_add_entities(initial_entities)


class GeckoShadowMetricSensor(
    GeckoEntityAvailabilityMixin, CoordinatorEntity, SensorEntity
):
    """Numeric metric parsed from device shadow outside modeled zone types."""

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        config_entry: ConfigEntry,
        metric_path: str,
    ) -> None:
        SensorEntity.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        self._metric_path = metric_path
        self._config_entry = config_entry

        vessel_slug = coordinator.vessel_name.lower().replace(" ", "_").replace(
            "-", "_"
        )
        path_slug = metric_path_to_entity_slug(metric_path)
        self._attr_name = (
            f"{coordinator.vessel_name} {_humanize_metric_name(metric_path)}"
        )
        self._attr_extra_state_attributes = {"shadow_path": metric_path}
        self._attr_unique_id = (
            f"{config_entry.entry_id}_{coordinator.monitor_id}_"
            f"{metric_path.replace('.', '_')}"
        )
        self.entity_id = f"sensor.{vessel_slug}_{path_slug}"

        dc, unit = infer_sensor_metadata(metric_path)
        if dc == "ph":
            self._attr_device_class = SensorDeviceClass.PH
            self._attr_suggested_display_precision = 2
        elif dc == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_state_class = SensorStateClass.MEASUREMENT

        if unit:
            self._attr_native_unit_of_measurement = unit

        if _metric_enabled_by_default(metric_path):
            self._attr_entity_registry_enabled_default = True
            self._attr_entity_category = None
        else:
            self._attr_entity_registry_enabled_default = False
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

        self._attr_icon = "mdi:gauge"
        self._attr_native_value = coordinator.get_shadow_metric_value(metric_path)

        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(coordinator.vessel_id))},
        )
        self._attr_available = False

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh value when coordinator or shadow metrics update."""
        self._attr_native_value = self.coordinator.get_shadow_metric_value(
            self._metric_path
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Register availability mixin and set initial value."""
        await super().async_added_to_hass()
        self._attr_native_value = self.coordinator.get_shadow_metric_value(
            self._metric_path
        )
