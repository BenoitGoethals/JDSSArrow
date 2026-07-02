"""Vol IV — exchange engine over the loopback transport."""

import asyncio

from jdssarrow.datamodel.codec.json_codec import JsonCodec
from jdssarrow.datamodel.messages import ChatMessage, JdssMessage, MessageHeader
from jdssarrow.iem.exchange import ExchangeEngine
from jdssarrow.iem.transport_loopback import LoopbackTransport
from jdssarrow.security.provider import NullSecurity


class _Collector:
    subscribes_to = ("*",)

    def __init__(self):
        self.received: list[JdssMessage] = []

    async def handle(self, message: JdssMessage) -> None:
        self.received.append(message)


def _engine(node_id: str, collector: _Collector, repeat: int = 3) -> ExchangeEngine:
    eng = ExchangeEngine(
        node_id=node_id,
        transport=LoopbackTransport(group="g"),
        codec=JsonCodec(),
        security=NullSecurity(),
        repeat=repeat,
    )
    eng.add_handler(collector)
    return eng


async def test_two_engines_exchange_and_dedup():
    rx_a, rx_b = _Collector(), _Collector()
    a = _engine("node-a", rx_a, repeat=3)
    b = _engine("node-b", rx_b, repeat=3)
    await a.start()
    await b.start()
    try:
        msg = JdssMessage(header=MessageHeader(originator_id="node-a"), body=ChatMessage(text="hi"))
        await a.publish(msg)
        await asyncio.sleep(0.05)

        # B receives exactly once despite repeat=3 (dedup), A does not hear its own message.
        assert len(rx_b.received) == 1
        assert rx_b.received[0].body.text == "hi"
        assert rx_a.received == []
    finally:
        await a.stop()
        await b.stop()


async def test_sequence_is_assigned():
    rx = _Collector()
    a = _engine("node-a", _Collector())
    b = _engine("node-b", rx)
    await a.start()
    await b.start()
    try:
        for text in ("1", "2"):
            hdr = MessageHeader(originator_id="node-a")
            await a.publish(JdssMessage(header=hdr, body=ChatMessage(text=text)))
        await asyncio.sleep(0.05)
        seqs = [m.header.sequence for m in rx.received]
        assert seqs == [1, 2]
    finally:
        await a.stop()
        await b.stop()
