"""Plugin registry: entry-point discovery + runtime swap (pluggability proof)."""

import asyncio

import pytest

from jdssarrow.config.models import GatewayConfig, NetworkConfig, NodeIdentity, PluginSelection
from jdssarrow.datamodel.messages import JdssMessage
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.plugins.registry import PluginError, Registry


def test_entry_points_discovered():
    r = Registry()
    assert "xml" in r.names("codecs")
    assert "loopback" in r.names("transports")
    assert "psk" in r.names("security")
    assert "default" in r.names("allocators")


def test_unknown_plugin_raises():
    r = Registry()
    with pytest.raises(PluginError):
        r.get("codecs", "does-not-exist")


class _CollectorHandler:
    subscribes_to = ("*",)

    def __init__(self):
        self.seen: list[JdssMessage] = []

    async def handle(self, message: JdssMessage) -> None:
        self.seen.append(message)


async def test_custom_codec_swapped_in_without_core_change():
    """Register a brand-new codec at runtime and run the whole gateway on it."""
    calls = {"encode": 0, "decode": 0}

    from jdssarrow.datamodel.codec.json_codec import JsonCodec

    class CountingCodec(JsonCodec):
        name = "counting"
        content_type = "application/x-counting+json"

        def encode(self, message):
            calls["encode"] += 1
            return super().encode(message)

        def decode(self, raw):
            calls["decode"] += 1
            return super().decode(raw)

    registry = Registry()
    registry.register("codecs", "counting", CountingCodec)

    def cfg(node_id, callsign):
        return GatewayConfig(
            identity=NodeIdentity(node_id=node_id, callsign=callsign),
            plugins=PluginSelection(transport="loopback", codec="counting", security="null"),
            network=NetworkConfig(network_id="swap-test"),
        )

    gw_a = JdssGateway(cfg("node-a", "A"), registry=registry)
    gw_b = JdssGateway(cfg("node-b", "B"), registry=registry)
    node_a, node_b = SoldierNode(gw_a), SoldierNode(gw_b)
    rx = _CollectorHandler()
    node_b.add_handler(rx)

    await node_a.start()
    await node_b.start()
    try:
        await node_a.chat("via custom codec")
        await asyncio.sleep(0.05)
        assert len(rx.seen) == 1
        assert calls["encode"] >= 1 and calls["decode"] >= 1
    finally:
        await node_a.stop()
        await node_b.stop()
