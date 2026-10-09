"""Connection lifecycle across reconnects.

A reconnect replaces the GeckoIotClient. The client it replaces must be
stopped, and nothing it emits afterwards may reach the shared connection:
not its connectivity, not its zones. Before this was enforced, every
reconnect after a transport drop left the old client running, and each old
client kept flipping `connection.is_connected` and could overwrite the
coordinator's zones with its own, never-updated copies.
"""

from __future__ import annotations

from custom_components.gecko.connection_manager import async_get_connection_manager

from .conftest import MONITOR_ID, build_zones


async def _connect(hass, network, received: list):
    manager = await async_get_connection_manager(hass)
    connection = await manager.async_get_or_create_connection(
        monitor_id=MONITOR_ID,
        websocket_url="wss://first",
        vessel_name="Spa",
        update_callback=received.append,
        refresh_token_callback=lambda monitor_id=None: "wss://fresh",
    )
    return manager, connection, network.clients[-1]


async def test_reconnect_after_a_drop_stops_the_dropped_client(hass, network):
    manager, connection, first = await _connect(hass, network, [])
    first.emit_connectivity(False)
    assert connection.is_connected is False

    assert await manager.async_reconnect_monitor(MONITOR_ID)

    second = network.clients[-1]
    assert second is not first
    assert connection.gecko_client is second
    assert first.disconnect_calls == 1, "the dropped client was left running"


async def test_dropped_client_cannot_mark_the_new_connection_down(hass, network):
    manager, connection, first = await _connect(hass, network, [])
    first.emit_connectivity(False)
    assert await manager.async_reconnect_monitor(MONITOR_ID)
    assert connection.is_connected is True

    # The replaced client reports a drop (a late callback, or its own retry loop).
    first.emit_connectivity(False)

    assert connection.is_connected is True


async def test_dropped_client_zones_do_not_reach_the_coordinator(hass, network):
    received: list = []
    manager, connection, first = await _connect(hass, network, received)
    first.emit_connectivity(False)
    fresh = build_zones({"flow": {"1": {"active": True}}})
    network.next_zones_on_connect = fresh
    assert await manager.async_reconnect_monitor(MONITOR_ID)
    assert received[-1] is fresh

    stale = build_zones({"flow": {"1": {"active": False}}})
    first.emit_zones(stale)

    assert received[-1] is fresh, "a replaced client overwrote the current zones"


async def test_current_client_drop_still_marks_the_connection_down(hass, network):
    """Over-correction guard: ignoring old clients must not ignore the current one."""
    manager, connection, first = await _connect(hass, network, [])
    first.emit_connectivity(False)
    assert await manager.async_reconnect_monitor(MONITOR_ID)
    second = network.clients[-1]

    second.emit_connectivity(False)

    assert connection.is_connected is False


async def test_new_client_zones_emitted_during_connect_are_delivered(hass, network):
    """Over-correction guard: the new client's first zones arrive inside connect()."""
    received: list = []
    manager, connection, first = await _connect(hass, network, received)
    first.emit_connectivity(False)
    fresh = build_zones({"flow": {"1": {"active": True}}})
    network.next_zones_on_connect = fresh

    assert await manager.async_reconnect_monitor(MONITOR_ID)

    assert received and received[-1] is fresh


async def test_token_refresh_after_a_drop_stops_the_dropped_client(hass, network):
    manager, connection, first = await _connect(hass, network, [])
    first.emit_connectivity(False)

    assert await manager.async_refresh_connection_token(MONITOR_ID)

    assert connection.gecko_client is not first
    assert first.disconnect_calls == 1, "the dropped client was left running"


async def test_unload_stops_a_dropped_client(hass, network):
    manager, connection, first = await _connect(hass, network, [])
    first.emit_connectivity(False)

    await manager.async_disconnect_monitor(MONITOR_ID)

    assert first.disconnect_calls == 1, "unload left the dropped client running"
    assert manager.get_connection(MONITOR_ID) is None
