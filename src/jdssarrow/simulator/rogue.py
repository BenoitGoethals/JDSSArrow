"""A rogue / non-compliant client, for testing that the network rejects bad traffic.

A legitimate JDSS client cannot emit non-conformant traffic (everything routes through the
gateway). The rogue deliberately bypasses that discipline to exercise each rejection boundary:

* ``wrong_key`` — structurally valid JDSSDM messages, but signed with an **unauthorised
  pre-shared key**. Rejected by **Vol I security** (HMAC verify fails).
* ``garbage``  — arbitrary bytes that are not even a JDSS frame. Rejected by **Vol IV
  framing** (bad magic).
* ``insider``  — the coalition key has leaked, so frames authenticate, but the payload is
  **not conformant JDSSDM**. Rejected by **Vol II** (codec/schema decode fails).

In every case the receivers drop the frame (``decode`` reason) and the rogue never appears in
the common operational picture, while legitimate traffic is unaffected.
"""

from __future__ import annotations

from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.iem.exchange import Frame
from jdssarrow.interfaces import Transport
from jdssarrow.security.provider import PreSharedKeySecurity

ROGUE_MODES = ("wrong_key", "garbage", "insider")


class RogueClient:
    role = "rogue"

    def __init__(
        self,
        gateway: JdssGateway,
        mode: str,
        coalition_psk: str,
        node_id: str = "rogue-1",
    ) -> None:
        if mode not in ROGUE_MODES:
            raise ValueError(f"unknown rogue mode {mode!r}; choose from {ROGUE_MODES}")
        self.gateway = gateway
        self.node = SoldierNode(gateway)  # used by wrong_key mode (goes via the engine)
        self.mode = mode
        self._coalition_psk = coalition_psk
        self.node_id = node_id
        self.frames_sent = 0

    async def start(self) -> None:
        await self.node.start()

    async def stop(self) -> None:
        await self.node.stop()

    def _transport(self) -> Transport:
        return self.gateway.bearer.transport()

    async def step(self, tick: int) -> None:
        if self.mode == "wrong_key":
            # valid message, but the engine signs it with the rogue's (wrong) key
            await self.node.presence(50.0 + tick * 0.001, 4.0, battery_pct=100)
        elif self.mode == "garbage":
            junk = b"\x13\x37 rogue junk not-a-jdss-frame " + bytes([tick % 256])
            await self._transport().send(junk)
        elif self.mode == "insider":
            # correct key (leaked) but a non-JDSSDM payload → passes security, fails the codec
            sec = PreSharedKeySecurity(self._coalition_psk)
            wire = sec.protect(b"<garbage>not-jdssdm</garbage>")
            await self._transport().send(Frame("xml", "psk", wire).to_bytes())
        self.frames_sent += 1
