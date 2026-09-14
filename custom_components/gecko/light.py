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

    gecko_iot_client's LightingZone model already exposes RGB color
    (zone.rgbi, zone.set_color(r, g, b, i)); this entity surfaces that as
    ColorMode.RGB instead of only ColorMode.ONOFF.

    It also surfaces zone.effect / zone.set_effect(). The Gecko API only
    validates effect names by length (1-50 characters) - there is no fixed
    enum of valid names anywhere in the client library or in Gecko's public
    documentation. To avoid offering effect names that the device might not
    actually accept, effect_list is not hardcoded; instead it is built up
    from effect names actually reported by the device for this zone (e.g.
    ones set previously via the Gecko app or physical keypad), and grows as
    more are observed. The currently active effect is always included even
    before this discovery happens.

    Two known limitations, both worth calling out explicitly:

    - zone.set_color() in gecko_iot_client currently passes its internal
      RGB object straight to _publish_desired_state() instead of calling
      RGB.model_dump() first, so every call fails with "Object of type RGB
      is not JSON serializable" (confirmed via debug log against a live
      device). This is a bug in gecko_iot_client itself, not in this
      integration - see https://github.com/geckoal/gecko-iot-client
      (file/link the corresponding issue there). Until that's fixed
      upstream, this entity builds the {"rgbi": [r, g, b, i]} payload
      itself as a plain JSON-safe list (matching the wire format the
      device itself uses when reporting rgbi) and calls the zone's
      _publish_desired_state() directly, bypassing the broken
      serialization in set_color() while keeping zone.rgbi/zone.active
      updated the same way set_color() would.
    - This platform has no separate brightness channel - the device
      implements it by scaling r/g/b themselves. Confirmed by watching
      the physical control panel's brightness dial against debug logs:
      every step moves r, g, AND b proportionally (e.g. [152,0,102,1] ->
      [18,0,12,0.1176], where 18/152 and 12/102 both match the new i).
      The 4th "i" value the device reports alongside r/g/b is NOT
      reliably controllable via the API, though: sending a specific i
      sometimes round-trips back exactly, sometimes comes back
      noticeably different, and sometimes comes back essentially
      inverted (e.g. sending i=1.0 for a color once came back reported
      as i=0.0196) - all confirmed live, with r/g/b always exactly as
      sent regardless. No pattern that would let i be sent reliably was
      found; it appears to depend on some internal device state this
      integration has no visibility into.

      Given that, this entity never sends a specific i - it always
      sends i=1 - and never trusts the device's reported i either.
      Instead, brightness is derived entirely from the (reliable) r/g/b
      values themselves: on read, brightness = max(r, g, b), and the
      base color is r/g/b scaled up so its largest channel is 255 (the
      device only ever reports the final, already-dimmed integer r/g/b,
      never the original full-value color, so this is a reconstruction,
      not a stored value). On write, r/g/b are computed as
      base_color * (brightness / 255), with i always 1.

      Reconstructing the base color from rounded integers on every read
      would still accumulate rounding error over repeated adjustments
      (confirmed with the earlier i-based approach), so this entity
      keeps its own shadow cache (self._cached_base_rgb /
      self._cached_brightness) of the exact, full-precision color it
      last asked the device to set, and works from that instead of
      re-deriving it each time. _update_state() reconciles this cache
      against each new device report: if the reported r/g/b still
      matches what the cached values * (cached_brightness/255) would
      produce, the update just confirms this entity's own last change
      and the full-precision cached values are kept; otherwise (physical
      panel, the Gecko app, an effect, or the very first read) the cache
      resets to adopt the device's own reported values (rescaled to a
      255 peak) as the new ground truth. This keeps repeated Home
      Assistant-only adjustments precise, while still correctly picking
      up changes made elsewhere.
      Separately, Home Assistant's own RGB color wheel widget has been
      observed sending a default white rgb_color alongside a very low
      brightness value when the currently-displayed color is very dark
      (confirmed live) - likely because a near-black color has no
      well-defined position on the wheel. That's a Home Assistant
      frontend quirk independent of the above; this entity does not
      attempt to detect or work around it, since doing so reliably would
      mean guessing at user intent.
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

        # Shadow cache of the last color/brightness we ourselves sent, at
        # full precision - see _update_state() and async_turn_on() for
        # why. None until the first state read. cached_base_rgb always
        # has a 255 peak; cached_brightness is 0-255, matching HA's own
        # brightness attribute directly (unlike the old i-based
        # approach, no separate unit conversion is needed here).
        self._cached_base_rgb: tuple[int, int, int] | None = None
        self._cached_brightness: int | None = None

        # The (base, brightness) pair that was current immediately
        # before the last change this entity made - see
        # _update_state() for why this is needed to tell a genuinely
        # external change apart from a late/reordered echo of the
        # device's OWN prior state.
        self._previous_base_rgb: tuple[int, int, int] | None = None
        self._previous_brightness: int | None = None

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

            # Pull color/brightness from zone.rgbi when available. The
            # reported "i" value is ignored entirely - see class
            # docstring for why it isn't reliably controllable via the
            # API. Brightness and color are derived purely from r/g/b
            # instead: brightness = max(r, g, b); base color = r/g/b
            # scaled up so the largest channel is 255.
            #
            # Reconstructing that base color from the device's rounded
            # integers on every single read would still accumulate
            # rounding error over repeated Home Assistant-driven
            # adjustments (this was confirmed with the earlier i-based
            # approach, and the same rounding math applies here). So
            # this entity keeps its own shadow cache
            # (self._cached_base_rgb / self._cached_brightness) of the
            # exact, full-precision color/brightness it last asked the
            # device to set, and works from that instead of re-deriving
            # anything from the rounded report. Each time a new device
            # state comes in, that cache is checked against the reported
            # r/g/b: if round(cached_base * cached_brightness/255) still
            # matches what the device reports, this update just confirms
            # this entity's own last change, and the cached
            # full-precision values are kept. Otherwise (physical panel,
            # the Gecko app, an effect, or the very first read) the
            # cache resets to adopt the device's own reported values
            # (rescaled to a 255 peak) as the new ground truth. That
            # rescaling happens once per external change, not on every
            # read, so it doesn't accumulate - only the single rounding
            # step already baked into the device's own reported
            # integers.
            rgbi = getattr(zone, "rgbi", None)
            if rgbi is not None:
                device_rgb = (rgbi.r, rgbi.g, rgbi.b)
                device_max = max(device_rgb)

                cache_confirmed = False
                predicted_rgb = None
                if self._cached_base_rgb is not None and self._cached_brightness is not None:
                    scale = self._cached_brightness / 255
                    predicted_rgb = tuple(round(component * scale) for component in self._cached_base_rgb)
                    cache_confirmed = predicted_rgb == device_rgb

                # The device appears to deliver updates via two
                # separate channels: periodic full-state snapshots, and
                # the confirmation of a specific change this entity just
                # requested. These can arrive out of order - confirmed
                # live: a snapshot still showing the state from BEFORE
                # this entity's last change arrived after that change
                # was sent but before its own confirmation did. Treating
                # that stale snapshot as an external change would wipe
                # the cache just before the real confirmation arrives,
                # which would then also "fail" to confirm against the
                # now-corrupted cache - a spurious double mismatch from
                # a single reordered message.
                #
                # To tell that apart from a genuine external change
                # (physical panel, the Gecko app, an effect), this also
                # checks the report against the PREVIOUS (base,
                # brightness) pair - the one that was current right
                # before this entity's last change. If the report only
                # matches that older pair, it's a late/reordered echo of
                # a state this entity itself already moved past, not a
                # new external change - so it's ignored and the current
                # cache is kept as-is, to be confirmed by the real
                # confirmation once it arrives.
                stale_echo = False
                if not cache_confirmed and self._previous_base_rgb is not None and self._previous_brightness is not None:
                    previous_scale = self._previous_brightness / 255
                    predicted_previous_rgb = tuple(
                        round(component * previous_scale) for component in self._previous_base_rgb
                    )
                    stale_echo = predicted_previous_rgb == device_rgb

                _LOGGER.debug(
                    "_update_state reconciliation for %s: device_rgb=%s cached_base_rgb=%s "
                    "cached_brightness=%s predicted_rgb=%s cache_confirmed=%s stale_echo=%s "
                    "previous_base_rgb=%s previous_brightness=%s",
                    self._attr_name, device_rgb, self._cached_base_rgb, self._cached_brightness,
                    predicted_rgb, cache_confirmed, stale_echo,
                    self._previous_base_rgb, self._previous_brightness,
                )

                if stale_echo:
                    # Late echo of a state this entity already moved
                    # past - ignore it and keep the current cache
                    # exactly as-is.
                    pass
                elif not cache_confirmed:
                    # First read, or a genuine external change
                    # (panel/app/effect) - adopt the device's reported
                    # values as the new ground truth.
                    if device_max > 0:
                        rescale = 255 / device_max
                        self._cached_base_rgb = tuple(
                            min(255, round(component * rescale)) for component in device_rgb
                        )
                    else:
                        # device_max == 0 means the device reported pure
                        # black - there is no base color to recover from
                        # that, so fall back to the raw (black) values.
                        self._cached_base_rgb = device_rgb
                    self._cached_brightness = device_max

                self._attr_rgb_color = self._cached_base_rgb
                self._attr_brightness = self._cached_brightness
            else:
                self._attr_rgb_color = None
                self._attr_brightness = None
                self._cached_base_rgb = None
                self._cached_brightness = None
                self._previous_base_rgb = None
                self._previous_brightness = None

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
        _LOGGER.debug("async_turn_on called for %s with kwargs=%s", self._attr_name, kwargs)
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
                _LOGGER.debug("Calling zone.set_effect(%r) for %s", effect, self._attr_name)
                # Offloaded to the executor: this ends up calling
                # gecko_iot_client's _publish_if_connected(), which
                # blocks synchronously on future.result(timeout=5.0)
                # waiting for MQTT delivery confirmation. Calling that
                # directly here would block Home Assistant's entire
                # event loop for the duration of that wait on every
                # single call - very noticeable when many calls arrive
                # in quick succession (e.g. dragging a color wheel),
                # where it can make the UI appear to stall and jump
                # between states as queued-up blocked calls finally
                # complete in a burst.
                await self.hass.async_add_executor_job(set_effect_method, effect)
                self._known_effects.add(effect)
                return

            # Touch color and/or brightness when HA asked for either
            # here. A plain "turn on" (neither in kwargs) falls through
            # to activate() below instead, so it doesn't guess at a
            # color or risk the device rejecting a combined
            # activate+color update.
            #
            # Brightness on this platform is not a separate channel -
            # the device scales r/g/b themselves to achieve it, and its
            # own "i" value can't be reliably controlled via the API
            # (see class docstring), so i is always sent as 1 here and
            # brightness is achieved purely by scaling r/g/b. The three
            # cases below all work from self._cached_base_rgb /
            # self._cached_brightness - the exact, full-precision values
            # this entity itself last set (kept in sync with the device
            # by _update_state()) - rather than from the device's own
            # rounded r/g/b report, so repeated adjustments don't
            # accumulate rounding error:
            # - color only: the given color becomes the new base,
            #   scaled by the current (cached) brightness.
            # - brightness only: the current (cached) base color is
            #   scaled by the new brightness.
            # - both at once (e.g. a script/automation): the given
            #   color is the new base, scaled by the given brightness.
            rgb_color = kwargs.get("rgb_color")
            brightness = kwargs.get("brightness")

            if (rgb_color is not None or brightness is not None) and hasattr(zone, "rgbi"):
                base_rgb = rgb_color if rgb_color is not None else (
                    self._cached_base_rgb if self._cached_base_rgb is not None else (255, 255, 255)
                )
                new_brightness = brightness if brightness is not None else (
                    self._cached_brightness if self._cached_brightness is not None else 255
                )
                scale = new_brightness / 255
                r, g, b = (round(component * scale) for component in base_rgb)
                i = 1

                # Workaround for a gecko_iot_client bug: zone.set_color()
                # passes its internal RGB object straight to
                # _publish_desired_state() without calling
                # RGB.model_dump() first, which fails with "Object of
                # type RGB is not JSON serializable" on every call (see
                # class docstring). Build the same update set_color()
                # would, but with a JSON-safe plain payload for the
                # publish call, and update zone.rgbi/zone.active the same
                # way set_color() does so the rest of the library still
                # sees consistent in-memory state.
                #
                # The payload is a list [r, g, b, i], not a dict -
                # matching the wire format the device itself uses when
                # reporting rgbi (see zone_parser.py, which parses rgbi
                # as a list). Sending a dict here is accepted by the
                # backend without error but appears to be silently
                # ignored by the device firmware - confirmed via debug
                # log against a live device, where color changes sent as
                # {"r":.., "g":.., ...} never showed up in the device's
                # subsequent state echoes, while the same request as a
                # list did.
                from gecko_iot_client.models.lighting_zone import RGB

                rgbi_payload = [r, g, b, i]

                _LOGGER.debug(
                    "Setting color for %s: rgbi_payload=%s (rgb_color kwarg=%s, brightness kwarg=%s, "
                    "base_rgb=%s, new_brightness=%s)",
                    self._attr_name, rgbi_payload, rgb_color, brightness, base_rgb, new_brightness,
                )
                # Update the shadow cache to the exact values just sent,
                # BEFORE the device's echo comes back - so the next
                # _update_state() confirms against these, not stale
                # ones. The previous values are kept too, so
                # _update_state() can recognize a late echo of that
                # older state as stale rather than a new external
                # change - see its comments for why.
                self._previous_base_rgb = self._cached_base_rgb
                self._previous_brightness = self._cached_brightness
                self._cached_base_rgb = tuple(base_rgb)
                self._cached_brightness = new_brightness
                zone.rgbi = RGB(r=r, g=g, b=b, i=i)
                zone.active = True
                # Offloaded to the executor - see the set_effect() call
                # above for why (this hits the same blocking
                # future.result() path in gecko_iot_client).
                await self.hass.async_add_executor_job(
                    zone._publish_desired_state, {"rgbi": rgbi_payload, "active": True}
                )
                _LOGGER.debug("Publish call for %s returned without raising", self._attr_name)
                return

            _LOGGER.debug("Plain turn_on (no color) for %s - calling activate()", self._attr_name)
            activate_method = getattr(zone, "activate", None)
            if activate_method and callable(activate_method):
                # Offloaded to the executor - see the set_effect() call
                # above for why.
                await self.hass.async_add_executor_job(activate_method)
            else:
                _LOGGER.warning("Zone %s does not have activate method", zone.id)
        except Exception as e:
            _LOGGER.error("Error turning on light %s: %s", self._attr_name, e)

    async def async_turn_off(self, **kwargs) -> None:
        """Turn the light off."""
        _LOGGER.debug("async_turn_off called for %s", self._attr_name)
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
                    # Offloaded to the executor - see the set_effect()
                    # call in async_turn_on() for why.
                    await self.hass.async_add_executor_job(deactivate_method)
                else:
                    _LOGGER.warning("Zone %s does not have deactivate method", zone.id)
            else:
                _LOGGER.warning("Could not find lighting zone %s", self._zone.id)
        except Exception as e:
            _LOGGER.error("Error turning off light %s: %s", self._attr_name, e)
