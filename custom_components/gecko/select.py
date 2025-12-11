"""Select entities for Gecko spa integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GeckoVesselCoordinator
from .entity import GeckoEntityAvailabilityMixin

_LOGGER = logging.getLogger(__name__)

# Map operation modes to user-friendly names (matches OperationModeController.mode_name)
WATERCARE_MODE_OPTIONS = [
    "Away",
    "Standard", 
    "Savings",
    "Super Savings",
    "Weekender",
    "Other"
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gecko select entities."""
    _LOGGER.debug("Setting up Gecko select entities")
    
    # Get runtime data with per-vessel coordinators
    runtime_data = entry.runtime_data
    if not runtime_data or not runtime_data.coordinators:
        _LOGGER.error("No coordinators found in runtime_data for config entry %s", entry.entry_id)
        return
    
    entities = []
    
    # Create a watercare mode select for each vessel coordinator
    for coordinator in runtime_data.coordinators:
        _LOGGER.debug("Creating watercare select for vessel %s (ID: %s)", 
                     coordinator.vessel_name, coordinator.vessel_id)
        
        # Add watercare mode select for each spa/vessel
        entities.append(
            GeckoWatercareSelectEntity(
                coordinator=coordinator,
                vessel_name=coordinator.vessel_name,
                vessel_id=coordinator.vessel_id,
            )
        )
    
    if entities:
        _LOGGER.debug("Adding %d Gecko select entities", len(entities))
        async_add_entities(entities)


class GeckoWatercareSelectEntity(GeckoEntityAvailabilityMixin, CoordinatorEntity, SelectEntity):
    """Representation of a Gecko watercare mode select."""

    def __init__(
        self,
        coordinator: GeckoVesselCoordinator,
        vessel_name: str,
        vessel_id: str,
    ) -> None:
        """Initialize the select."""
        SelectEntity.__init__(self)
        CoordinatorEntity.__init__(self, coordinator)
        
        self._vessel_name = vessel_name
        self._vessel_id = vessel_id
        
        # Set up entity attributes
        self._attr_name = f"{vessel_name} Watercare Mode"
        self._attr_unique_id = f"{vessel_id}_watercare_mode"
        self._attr_icon = "mdi:hot-tub"
        self._attr_entity_category = EntityCategory.CONFIG
        self._attr_options = WATERCARE_MODE_OPTIONS
        
        # Device info for grouping entities
        self._attr_device_info = dr.DeviceInfo(
            identifiers={(DOMAIN, str(vessel_id))},
        )
        
        # Initialize state
        self._attr_current_option = None
        
        # Initialize availability (will be updated by mixin when added to hass)
        self._attr_available = False

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        # Call parent classes - this ensures the mixin's connectivity registration happens
        await super().async_added_to_hass()
        # Update state immediately when added
        await self._async_update_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        try:
            # Schedule async state update
            self.hass.async_create_task(self._async_update_state())
        except Exception as e:
            _LOGGER.debug("Error scheduling state update for %s: %s", self._attr_name, e)

    async def _async_update_state(self) -> None:
        """Update the select state asynchronously."""
        try:
            # Get the gecko client for this vessel's monitor
            gecko_client = await self.coordinator.get_gecko_client()
            
            if gecko_client and gecko_client.operation_mode_controller:
                # Use the clean API to get current mode name
                self._attr_current_option = gecko_client.operation_mode_controller.mode_name
                _LOGGER.debug("Updated watercare mode for %s: %s", self._attr_name, self._attr_current_option)
            else:
                _LOGGER.debug("Gecko client or operation mode controller not available for %s", self._attr_name)
                self._attr_current_option = None
                
        except Exception as e:
            _LOGGER.debug("Could not get operation mode for %s: %s", self._attr_name, e)
            self._attr_current_option = None
        
        # Availability is now updated via CONNECTIVITY_UPDATE events, not polling
        
        # Write the updated state
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option not in WATERCARE_MODE_OPTIONS:
            _LOGGER.error("Invalid watercare mode option: %s", option)
            return
            
        _LOGGER.info("Setting watercare mode for %s to %s", self._attr_name, option)
        
        try:
            # Get the gecko client for this vessel
            gecko_client = await self.coordinator.get_gecko_client()
            
            if not gecko_client:
                _LOGGER.error("No gecko client available for vessel %s", self._vessel_name)
                return
                
            if not gecko_client.operation_mode_controller:
                _LOGGER.error("Operation mode controller not available for vessel %s", self._vessel_name)
                return
                
            _LOGGER.debug("Setting operation mode to %s for vessel %s", option, self._vessel_name)
            
            # Use the clean API to set the mode by name
            gecko_client.operation_mode_controller.set_mode_by_name(option)
            
            # Let the coordinator update handle state changes
            _LOGGER.debug("Sent watercare mode command for %s", self._attr_name)
            
            # Request coordinator refresh to get updated state from the device
            await self.coordinator.async_request_refresh()
                
        except Exception as e:
            _LOGGER.error("Error setting watercare mode for %s: %s", self._attr_name, e)