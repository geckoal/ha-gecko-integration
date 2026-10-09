"""A pump zone that reports active without a speed.

GeckoFan only set `_attr_speed` from a numeric speed or when the zone was
inactive, and `_handle_coordinator_update` logs `self._attr_speed` before
anything else. A zone that is active with no speed therefore raised
AttributeError on every coordinator update, and the entity's state was never
written again.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from gecko_iot_client.models.zone_types import ZoneType

from custom_components.gecko.const import DOMAIN
from custom_components.gecko.coordinator import GeckoVesselCoordinator
from custom_components.gecko.fan import GeckoFan

from .conftest import MONITOR_ID, build_zones


async def test_active_pump_without_speed_updates(hass, network):
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1", data={})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(api_client=None, coordinators=[])
    coordinator = GeckoVesselCoordinator(hass, entry.entry_id, "vessel-1", MONITOR_ID, "Spa")
    network.next_zones_on_connect = build_zones({"flow": {"1": {"active": True}}})
    assert await coordinator.async_setup_monitor_connection("wss://first")
    await hass.async_block_till_done()
    zone = next(z for z in coordinator.get_zones_by_type(ZoneType.FLOW_ZONE) if z.id == "1")
    assert zone.active is True and zone.speed is None, "the fixture must be active without a speed"

    fan = GeckoFan(coordinator, entry, zone)
    with patch.object(fan, "async_write_ha_state") as write:
        fan._handle_coordinator_update()

    write.assert_called_once()
    assert fan.is_on is True
