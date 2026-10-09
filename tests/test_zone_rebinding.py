"""Entities follow the zones of the current client.

A reconnect builds a new GeckoIotClient, and the new client builds new zone
objects. An entity that keeps the zone object it was created with reads the
old client forever: its state freezes at the moment of the first reconnect,
and its commands go out through a client that is no longer connected.

The light already re-resolves its zone by id on every update and command;
these tests hold the pump and the thermostat to the same rule, and keep the
light as a control.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from gecko_iot_client.models.zone_types import ZoneType

from custom_components.gecko.climate import GeckoClimate
from custom_components.gecko.const import DOMAIN
from custom_components.gecko.coordinator import GeckoVesselCoordinator
from custom_components.gecko.fan import GeckoFan
from custom_components.gecko.light import GeckoLight

from .conftest import MONITOR_ID, build_zones, record_publishes


class FakeApi:
    """Answers the token refresh the way the Gecko API does: a fresh broker URL."""

    async def async_get_monitor_livestream(self, monitor_id):
        return {"brokerUrl": "wss://fresh"}


async def _setup(hass, network, zones):
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1", data={})
    entry.add_to_hass(hass)
    entry.runtime_data = SimpleNamespace(api_client=FakeApi(), coordinators=[])
    coordinator = GeckoVesselCoordinator(hass, entry.entry_id, "vessel-1", MONITOR_ID, "Spa")
    network.next_zones_on_connect = zones
    assert await coordinator.async_setup_monitor_connection("wss://first")
    await hass.async_block_till_done()
    return coordinator, entry


async def _drop_and_reconnect(hass, network, coordinator, zones):
    """The path production takes: transport drops, the coordinator reconnects."""
    network.clients[-1].emit_connectivity(False)
    network.next_zones_on_connect = zones
    await coordinator._simple_reconnect()
    await hass.async_block_till_done()
    assert len(network.clients) == 2, "the reconnect did not build a new client"


def _zone(coordinator, zone_type, zone_id):
    return next(z for z in coordinator.get_zones_by_type(zone_type) if z.id == zone_id)


def _coordinator_update(entity) -> None:
    with patch.object(entity, "async_write_ha_state"):
        entity._handle_coordinator_update()


async def test_pump_state_follows_the_new_client(hass, network):
    coordinator, entry = await _setup(hass, network, build_zones({"flow": {"1": {"active": False}}}))
    fan = GeckoFan(coordinator, entry, _zone(coordinator, ZoneType.FLOW_ZONE, "1"))
    assert fan.is_on is False

    await _drop_and_reconnect(
        hass, network, coordinator, build_zones({"flow": {"1": {"active": True, "speed": 50}}})
    )
    _coordinator_update(fan)

    assert fan.is_on is True, "the pump reads the zone of the replaced client"


async def test_pump_turn_off_goes_through_the_new_client(hass, network):
    old_zones = build_zones({"flow": {"1": {"active": True}}})
    new_zones = build_zones({"flow": {"1": {"active": True}}})
    published: list = []
    record_publishes(old_zones, published)
    record_publishes(new_zones, published)
    coordinator, entry = await _setup(hass, network, old_zones)
    fan = GeckoFan(coordinator, entry, _zone(coordinator, ZoneType.FLOW_ZONE, "1"))
    await _drop_and_reconnect(hass, network, coordinator, new_zones)

    await fan.async_turn_off()

    assert [(z, u) for z, _t, _i, u in published] == [
        (_zone(coordinator, ZoneType.FLOW_ZONE, "1"), {"active": False})
    ], "turn_off was sent through the replaced client"


async def test_pump_missing_from_the_new_client_is_not_commanded(hass, network):
    """Over-correction guard: no silent fallback to the stale zone object."""
    old_zones = build_zones({"flow": {"1": {"active": True, "speed": 50}}})
    published: list = []
    record_publishes(old_zones, published)
    coordinator, entry = await _setup(hass, network, old_zones)
    fan = GeckoFan(coordinator, entry, _zone(coordinator, ZoneType.FLOW_ZONE, "1"))
    new_zones = build_zones()
    new_zones[ZoneType.FLOW_ZONE] = [z for z in new_zones[ZoneType.FLOW_ZONE] if z.id != "1"]
    await _drop_and_reconnect(hass, network, coordinator, new_zones)

    _coordinator_update(fan)
    await fan.async_turn_off()

    assert fan.is_on is True, "a missing zone must keep the last known state"
    assert published == [], "turn_off fell back to the replaced client's zone"


async def test_thermostat_state_follows_the_new_client(hass, network):
    coordinator, entry = await _setup(
        hass, network, build_zones({"temperatureControl": {"1": {"temperature_": 38.0, "setPoint": 38}}})
    )
    climate = GeckoClimate(coordinator, _zone(coordinator, ZoneType.TEMPERATURE_CONTROL_ZONE, "1"))
    assert climate.current_temperature == 38.0

    await _drop_and_reconnect(
        hass,
        network,
        coordinator,
        build_zones({"temperatureControl": {"1": {"temperature_": 38.5, "setPoint": 38, "status_": "HEATING"}}}),
    )
    _coordinator_update(climate)

    assert climate.current_temperature == 38.5, "the thermostat reads the replaced client"
    assert climate.hvac_action == "heating"


async def test_thermostat_set_temperature_goes_through_the_new_client(hass, network):
    old_zones = build_zones({"temperatureControl": {"1": {"temperature_": 38.0, "setPoint": 38}}})
    new_zones = build_zones({"temperatureControl": {"1": {"temperature_": 38.0, "setPoint": 38}}})
    published: list = []
    record_publishes(old_zones, published)
    record_publishes(new_zones, published)
    coordinator, entry = await _setup(hass, network, old_zones)
    climate = GeckoClimate(coordinator, _zone(coordinator, ZoneType.TEMPERATURE_CONTROL_ZONE, "1"))
    climate.hass = hass
    await _drop_and_reconnect(hass, network, coordinator, new_zones)

    await climate.async_set_temperature(temperature=37.5)

    assert [(z, u) for z, _t, _i, u in published] == [
        (_zone(coordinator, ZoneType.TEMPERATURE_CONTROL_ZONE, "1"), {"setPoint": 37.5})
    ], "the setpoint was sent through the replaced client"


async def test_replaced_client_cannot_feed_stale_zones_to_entities(hass, network):
    coordinator, entry = await _setup(hass, network, build_zones({"flow": {"1": {"active": False}}}))
    fan = GeckoFan(coordinator, entry, _zone(coordinator, ZoneType.FLOW_ZONE, "1"))
    old_client = network.clients[0]
    await _drop_and_reconnect(hass, network, coordinator, build_zones({"flow": {"1": {"active": True}}}))

    old_client.emit_zones(build_zones({"flow": {"1": {"active": False}}}))
    await hass.async_block_till_done()
    _coordinator_update(fan)

    assert fan.is_on is True, "the replaced client's zones replaced the current ones"


async def test_light_follows_the_new_client(hass, network):
    """Control: the light already resolves its zone per call, before and after the fix."""
    old_zones = build_zones({"lighting": {"1": {"active": False}}})
    new_zones = build_zones({"lighting": {"1": {"active": True}}})
    published: list = []
    record_publishes(old_zones, published)
    record_publishes(new_zones, published)
    coordinator, entry = await _setup(hass, network, old_zones)
    light = GeckoLight(coordinator, entry, _zone(coordinator, ZoneType.LIGHTING_ZONE, "1"))
    await _drop_and_reconnect(hass, network, coordinator, new_zones)

    _coordinator_update(light)
    await light.async_turn_off()

    assert light.is_on is True
    assert [(z, u) for z, _t, _i, u in published] == [
        (_zone(coordinator, ZoneType.LIGHTING_ZONE, "1"), {"active": False})
    ]
