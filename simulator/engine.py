"""External JDSS simulator engine — connects units to a JDSS gateway and drives scenarios.

For each scenario unit this builds a real :class:`jdssarrow.gateway.gateway.JdssGateway` and joins
the coalition network, either **secure** (``psk`` — HMAC-SHA256) or **non-secure** (``null``). It
then loops: every tick each unit advances along its route and emits the JDSS message set (Presence
with course/speed, Identification, ContactSighting on enemy positions, CasevacRequest, Chat orders,
Overlay objectives). Inbound coalition traffic (e.g. from the gateway or other clients) is reported
too, so the app shows both directions.

The engine is UI-agnostic: it pushes plain dicts to an ``on_event`` callback, which the PyQt6 app
turns into Qt signals. It has no Qt import, so it runs headless (and under pytest).
"""

from __future__ import annotations

import contextlib
import random
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from jdssarrow.config.models import (
    ClassificationConfig,
    GatewayConfig,
    GossipConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.datamodel import symbology
from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    ContactSighting,
    Identification,
    JdssMessage,
    Location,
    Overlay,
    OverlayGraphic,
    Presence,
)
from jdssarrow.gateway.gateway import JdssGateway
from simulator import geo
from simulator.scenarios import Scenario, UnitSpec

Event = dict


@dataclass
class LiveUnit:
    spec: UnitSpec
    gateway: JdssGateway
    lat: float
    lon: float
    wp: int = 1  # index of the waypoint we're heading toward
    course: float = 0.0
    speed: float = 0.0
    sent: int = 0
    cycles: int = 0  # full route traversals completed
    idx: int = 0  # position in the roster (for staggering behaviours)


class SimulatorEngine:
    def __init__(
        self,
        scenario: Scenario,
        *,
        secure: bool = True,
        transport: str = "udp",
        codec: str = "xml",
        network_id: str | None = None,
        psk: str = "jdss-coalition-key",
        classification: int = 1,
        on_event: Callable[[Event], None] | None = None,
        seed: int = 1337,
    ) -> None:
        self.scenario = scenario
        self.secure = secure
        self.transport = transport
        self.codec = codec
        self.network_id = network_id or scenario.network_id
        self.psk = psk
        self.classification = classification
        self._on_event = on_event or (lambda e: None)
        self._rng = random.Random(seed)
        self.units: list[LiveUnit] = []
        self._own_ids: set[str] = {u.node_id for u in scenario.units}
        self._seen: set[str] = set()
        self.sent = 0
        self.received = 0
        self.tick_no = 0

    # ------------------------------------------------------------------ lifecycle
    def _config(self, spec: UnitSpec) -> GatewayConfig:
        return GatewayConfig(
            identity=NodeIdentity(
                node_id=spec.node_id,
                callsign=spec.callsign,
                unit=spec.unit or self.scenario.name,
                nation=spec.nation,
                role=spec.role,
            ),
            plugins=PluginSelection(
                codec=self.codec,
                transport=self.transport,
                security="psk" if self.secure else "null",
            ),
            network=NetworkConfig(network_id=self.network_id, psk=self.psk, repeat=2),
            classification=ClassificationConfig(
                level=self.classification, releasable_to="COALITION"
            ),
            gossip=GossipConfig(enabled=False),
        )

    async def start(self) -> None:
        for idx, spec in enumerate(self.scenario.units):
            gateway = JdssGateway(self._config(spec))
            gateway.add_handler(_RxHandler(self))
            await gateway.start()
            lat, lon = spec.route[0]
            self.units.append(LiveUnit(spec=spec, gateway=gateway, lat=lat, lon=lon, idx=idx))
        for u in self.units:  # announce identity once
            await self._publish(
                u,
                Identification(
                    callsign=u.spec.callsign,
                    unit=u.spec.unit or self.scenario.name,
                    role=u.spec.role,
                    nation=u.spec.nation,
                ),
                "identify",
            )
            if "overlay" in u.spec.behaviors:
                await self._emit_objective(u)
        mode = "secure (PSK / HMAC-SHA256)" if self.secure else "NON-secure (null)"
        self._status(
            f"{len(self.units)} units joined '{self.network_id}' — {mode}, "
            f"{self.codec}/{self.transport}"
        )

    async def stop(self) -> None:
        for u in self.units:
            with contextlib.suppress(Exception):
                await u.gateway.stop()
        self.units = []
        self._status("stopped — units left the network")

    # ------------------------------------------------------------------ per-tick
    async def tick(self, dt: float, tick_no: int) -> None:
        self.tick_no = tick_no
        for u in self.units:
            self._advance(u, dt)
            await self._emit_presence(u)
            await self._behaviors(u, tick_no)
            self._on_event(
                {
                    "kind": "unit",
                    "node_id": u.spec.node_id,
                    "callsign": u.spec.callsign,
                    "nation": u.spec.nation,
                    "role": u.spec.role,
                    "lat": round(u.lat, 5),
                    "lon": round(u.lon, 5),
                    "course": round(u.course, 1),
                    "speed": round(u.speed, 1),
                    "sent": u.sent,
                    "cycles": u.cycles,
                }
            )
        self._on_event(
            {
                "kind": "stats",
                "nodes": len(self.units),
                "sent": self.sent,
                "received": self.received,
                "tick": tick_no,
            }
        )

    def _advance(self, u: LiveUnit, dt: float) -> None:
        route = u.spec.route
        if len(route) < 2:
            return
        remaining = u.spec.speed_mps * dt
        guard = 0
        while remaining > 0 and guard < 2 * len(route):
            guard += 1
            target = route[u.wp]
            d = geo.haversine((u.lat, u.lon), target)
            if d <= 1e-6:
                u.wp = (u.wp + 1) % len(route)
                if u.wp == 0:
                    u.cycles += 1
                continue
            if d <= remaining:
                u.lat, u.lon = target
                remaining -= d
                u.wp = (u.wp + 1) % len(route)
                if u.wp == 0:
                    u.cycles += 1
            else:
                u.course = geo.bearing((u.lat, u.lon), target)
                u.lat, u.lon = geo.dest_point((u.lat, u.lon), u.course, remaining)
                remaining = 0
        u.speed = u.spec.speed_mps

    async def _emit_presence(self, u: LiveUnit) -> None:
        body = Presence(
            location=Location(lat=u.lat, lon=u.lon),
            callsign=u.spec.callsign,
            battery_pct=self._rng.randint(55, 100),
            course_deg=round(u.course, 1),
            speed_mps=round(u.speed, 1),
            sidc=symbology.sidc(u.spec.entity, symbology.StandardIdentity.FRIEND),
        )
        await self._publish(u, body, f"crs {u.course:03.0f}° spd {u.speed:.1f} m/s")

    async def _behaviors(self, u: LiveUnit, tick_no: int) -> None:
        b = u.spec.behaviors
        stagger = (tick_no + u.idx) % 4
        if "contact" in b and stagger == 0 and self.scenario.enemies:
            enemy = self._rng.choice(self.scenario.enemies)
            body = ContactSighting(
                location=Location(lat=enemy.lat, lon=enemy.lon),
                identity=symbology.StandardIdentity.HOSTILE,
                description=enemy.name,
                strength=self._rng.randint(2, 20),
            )
            await self._publish(u, body, f"contact: {enemy.name}")
        if "casevac" in b and self._rng.random() < 0.05:
            body = CasevacRequest(
                location=Location(lat=u.lat, lon=u.lon),
                patients_urgent=self._rng.randint(1, 2),
            )
            await self._publish(u, body, "CASEVAC requested")
        if "chat" in b and (tick_no + u.idx) % 6 == 0 and self.scenario.orders:
            text = self._rng.choice(self.scenario.orders)
            await self._publish(u, ChatMessage(text=text), f"chat: {text[:40]}…")

    async def _emit_objective(self, u: LiveUnit) -> None:
        cx, cy = self.scenario.center
        body = Overlay(
            name=f"{self.scenario.name} — objective",
            graphics=[
                OverlayGraphic(
                    sidc=symbology.sidc("control_point"),
                    location=Location(lat=cx, lon=cy),
                    label="OBJ",
                )
            ],
        )
        await self._publish(u, body, "objective overlay")

    # ------------------------------------------------------------------ publish/receive
    async def _publish(self, u: LiveUnit, body: object, detail: str) -> None:
        try:
            await u.gateway.publish(body)
        except Exception as exc:  # capability/emit errors shouldn't kill the loop
            self._status(f"{u.spec.callsign}: publish failed — {exc}")
            return
        u.sent += 1
        self.sent += 1
        loc = getattr(body, "location", None)
        self._on_event(
            {
                "kind": "sent",
                "node_id": u.spec.node_id,
                "callsign": u.spec.callsign,
                "type": str(getattr(body, "type", "?")),
                "lat": getattr(loc, "lat", None),
                "lon": getattr(loc, "lon", None),
                "detail": detail,
                "ts": time.time(),
            }
        )

    def _note_received(self, message: JdssMessage) -> None:
        h = message.header
        if h.originator_id in self._own_ids or h.message_id in self._seen:
            return  # our own traffic / already counted
        self._seen.add(h.message_id)
        self.received += 1
        self._on_event(
            {
                "kind": "received",
                "from": h.originator_id,
                "callsign": getattr(message.body, "callsign", None),
                "type": str(message.type),
                "ts": time.time(),
            }
        )

    def _status(self, text: str) -> None:
        self._on_event({"kind": "status", "text": text})


class _RxHandler:
    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, engine: SimulatorEngine) -> None:
        self._engine = engine

    async def handle(self, message: JdssMessage) -> None:
        self._engine._note_received(message)
