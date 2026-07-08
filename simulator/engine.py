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

import asyncio
import contextlib
import json
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
    ChatRoom,
    Chatrooms,
    ContactSighting,
    GeneralInfo,
    Identification,
    JdssMessage,
    Location,
    MessageType,
    Overlay,
    OverlayGraphic,
    Presence,
    Receipt,
)
from jdssarrow.gateway.gateway import JdssGateway
from simulator import geo
from simulator.scenarios import Scenario, UnitSpec, expand_scenario

Event = dict

#: in stress mode, only this many unit rows are streamed to the UI (per-message logs are dropped)
#: so a 500-operator run doesn't flood the table/log — aggregate throughput is reported instead.
_STRESS_UI_SAMPLE = 40


@dataclass
class LiveUnit:
    spec: UnitSpec
    gateway: JdssGateway | None  # None in inject mode (no local node)
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
        mode: str = "inject",  # "inject" (HTTP → gateway) or "multicast" (join as UDP peers)
        gateway_url: str = "http://localhost:8000",
        on_event: Callable[[Event], None] | None = None,
        seed: int = 1337,
        stress: int = 0,  # >0 → expand the scenario to this many synthetic operators (load test)
        concurrency: int = 64,  # max in-flight publishes per tick (bounds the stress fan-out)
        auto_ack: bool = True,  # reply to received coalition traffic with a Receipt (duplex)
    ) -> None:
        # a stress run clones the scenario up to `stress` operators; otherwise use it as-is
        self.stress = stress if stress and stress > len(scenario.units) else 0
        self.scenario = expand_scenario(scenario, stress, seed=seed) if self.stress else scenario
        self.secure = secure
        self.transport = transport
        self.codec = codec
        self.network_id = network_id or self.scenario.network_id
        self.psk = psk
        self.classification = classification
        self.mode = mode
        self.gateway_url = gateway_url.rstrip("/")
        self._http = None
        self._on_event = on_event or (lambda e: None)
        self._rng = random.Random(seed)
        self._concurrency = max(1, concurrency)
        self._sem: asyncio.Semaphore | None = None  # created on the running loop in start()
        self.auto_ack = auto_ack
        self.units: list[LiveUnit] = []
        self._own_ids: set[str] = {u.node_id for u in self.scenario.units}
        self._seen: set[str] = set()
        self._ack_unit: LiveUnit | None = None  # designated originator for auto-ack Receipts
        self._ws_task: asyncio.Task | None = None  # inject-mode receive stream
        self._stopping = False
        self.sent = 0
        self.received = 0
        self.acked = 0
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
        self._sem = asyncio.Semaphore(self._concurrency)
        self._stopping = False
        if self.mode == "inject":
            await self._start_inject()
        else:
            await self._start_multicast()

        # designate one originator to answer received coalition traffic with a Receipt: prefer a
        # command-post-style unit (has "chat"), else the first unit.
        self._ack_unit = next(
            (u for u in self.units if "chat" in u.spec.behaviors),
            self.units[0] if self.units else None,
        )

        async def _announce(u: LiveUnit) -> None:  # identity + objective + rooms once, concurrently
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
            if "chat" in u.spec.behaviors:  # command-post units enumerate their GeoChat rooms
                await self._emit_chatrooms(u)

        await self._run_bounded(_announce(u) for u in self.units)

    async def _start_multicast(self) -> None:
        for idx, spec in enumerate(self.scenario.units):
            gateway = JdssGateway(self._config(spec))
            gateway.add_handler(_RxHandler(self))
            await gateway.start()
            lat, lon = spec.route[0]
            self.units.append(LiveUnit(spec=spec, gateway=gateway, lat=lat, lon=lon, idx=idx))
        mode = "secure (PSK / HMAC-SHA256)" if self.secure else "NON-secure (null)"
        self._status(
            f"{len(self.units)} units joined multicast '{self.network_id}' — {mode}, "
            f"{self.codec}/{self.transport}"
        )

    async def _start_inject(self) -> None:
        import httpx

        self._http = httpx.AsyncClient(timeout=5.0)
        for idx, spec in enumerate(self.scenario.units):
            lat, lon = spec.route[0]
            self.units.append(LiveUnit(spec=spec, gateway=None, lat=lat, lon=lon, idx=idx))
        try:
            r = await self._http.get(f"{self.gateway_url}/api/health")
            r.raise_for_status()
            node = r.json().get("node_id", "?")
            self._status(
                f"injecting {len(self.units)} units into gateway {self.gateway_url} "
                f"(node '{node}') — the gateway fans out to ATAK / TAK servers / dashboard"
            )
        except Exception as exc:
            self._status(f"WARNING: cannot reach gateway {self.gateway_url}/api/health — {exc}")
        # bi-directional: subscribe to the gateway's live feed so coalition traffic comes back in
        # (skipped under stress load, where 500 units' own injects would flood the feed for nothing)
        if self.auto_ack and not self.stress:
            self._ws_task = asyncio.create_task(self._ws_listen())

    async def stop(self) -> None:
        self._stopping = True
        if self._ws_task is not None:
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._ws_task
            self._ws_task = None
        for u in self.units:
            if u.gateway is not None:
                with contextlib.suppress(Exception):
                    await u.gateway.stop()
        if self._http is not None:
            with contextlib.suppress(Exception):
                await self._http.aclose()
            self._http = None
        self.units = []
        self._ack_unit = None
        self._status("stopped")

    # ------------------------------------------------------------------ bi-directional receive
    async def _ws_listen(self) -> None:
        """Inject mode: read the gateway's live event feed so we *receive* coalition traffic."""
        import websockets

        base = self.gateway_url.replace("https://", "wss://").replace("http://", "ws://")
        ws_url = base + "/ws/events"
        while not self._stopping:
            try:
                async with websockets.connect(ws_url, max_size=None) as ws:
                    self._status(f"receiving coalition traffic from {ws_url}")
                    async for raw in ws:
                        event = json.loads(raw)
                        if event.get("direction") in ("sent", "received"):
                            await self._receive(
                                event.get("originator_id"),
                                event.get("message_id"),
                                event.get("type"),
                                event.get("callsign"),
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # a dropped feed shouldn't kill the sim — retry
                if self._stopping:
                    return
                self._status(f"receive feed dropped ({exc}); reconnecting…")
                await asyncio.sleep(2.0)

    async def _receive(
        self, origin: str | None, mid: str | None, type_name: str | None, callsign: str | None
    ) -> None:
        """Handle one inbound message (from multicast RX or the inject-mode feed)."""
        if not origin or origin in self._own_ids or (mid and mid in self._seen):
            return  # our own traffic / already counted
        if mid:
            self._seen.add(mid)
        self.received += 1
        self._on_event(
            {"kind": "received", "from": origin, "callsign": callsign,
             "type": type_name, "ts": time.time()}
        )
        # bi-directional: acknowledge received coalition traffic with a Receipt (but never ack an
        # ack, or we'd loop) so the gateway/other clients see a reply flow back.
        if (
            self.auto_ack
            and mid
            and type_name != str(MessageType.RECEIPT)
            and self._ack_unit is not None
        ):
            await self._publish(
                self._ack_unit,
                Receipt(ack_message_id=mid, status="received", note=f"ack {origin}"),
                f"receipt → {origin}",
            )
            self.acked += 1

    async def _run_bounded(self, coros: Iterable) -> None:
        """Run coroutines concurrently, capped by the per-tick concurrency semaphore."""

        async def _guard(coro) -> None:
            assert self._sem is not None
            async with self._sem:
                await coro

        await asyncio.gather(*(_guard(c) for c in coros), return_exceptions=True)

    # ------------------------------------------------------------------ per-tick
    async def tick(self, dt: float, tick_no: int) -> None:
        self.tick_no = tick_no
        started = time.perf_counter()
        sent_before = self.sent

        for u in self.units:  # advance every track first (cheap, synchronous)
            self._advance(u, dt)

        async def _emit(u: LiveUnit) -> None:  # then fan the publishes out concurrently
            await self._emit_presence(u)
            await self._behaviors(u, tick_no)

        await self._run_bounded(_emit(u) for u in self.units)

        # stream unit rows to the UI — sampled under stress so the table doesn't churn 500 rows/tick
        rows = self.units[:_STRESS_UI_SAMPLE] if self.stress else self.units
        for u in rows:
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
        elapsed = time.perf_counter() - started
        self._on_event(
            {
                "kind": "stats",
                "nodes": len(self.units),
                "sent": self.sent,
                "received": self.received,
                "tick": tick_no,
                "rate": round((self.sent - sent_before) / elapsed, 1) if elapsed > 0 else 0.0,
                "elapsed_ms": round(elapsed * 1000),
                "stress": self.stress,
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
        if "chat" in b and (tick_no + u.idx) % 7 == 0 and tick_no:  # structured GenInfo bulletin
            await self._publish(
                u,
                GeneralInfo(
                    subject="SITREP",
                    text=f"{u.spec.callsign} on {self.scenario.name}: situation nominal",
                    location=Location(lat=u.lat, lon=u.lon),
                ),
                "geninfo: SITREP",
            )

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

    async def _emit_chatrooms(self, u: LiveUnit) -> None:
        body = Chatrooms(
            rooms=[
                ChatRoom(room_id="All Chat Rooms", name="All Chat Rooms"),
                ChatRoom(room_id=self.scenario.network_id, name=self.scenario.name,
                         members=[u.spec.node_id]),
            ]
        )
        await self._publish(u, body, "chatrooms enumeration")

    # ------------------------------------------------------------------ publish/receive
    async def _publish(self, u: LiveUnit, body: object, detail: str) -> None:
        try:
            if self.mode == "inject":
                resp = await self._http.post(  # type: ignore[union-attr]
                    f"{self.gateway_url}/api/inject", json=self._inject_payload(u, body)
                )
                resp.raise_for_status()
            else:
                await u.gateway.publish(body)  # type: ignore[union-attr]
        except Exception as exc:  # a failed send shouldn't kill the loop
            self._status(f"{u.spec.callsign}: send failed — {exc}")
            return
        u.sent += 1
        self.sent += 1
        if self.stress:
            return  # 500 operators would flood the log — throughput is reported via "stats"
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

    def _inject_payload(self, u: LiveUnit, body: object) -> dict:
        """Flatten a JDSS body into the /api/inject schema."""
        p: dict = {
            "originator": u.spec.node_id,
            "type": str(getattr(body, "type", "?")),
            "callsign": u.spec.callsign,
            "classification": self.classification,
        }
        loc = getattr(body, "location", None)
        if loc is not None:
            p["lat"], p["lon"] = loc.lat, loc.lon
        for f in ("course_deg", "speed_mps", "battery_pct", "description",
                  "strength", "text", "unit", "role", "nation", "sidc"):
            v = getattr(body, f, None)
            if v is not None:
                p[f] = v
        ident = getattr(body, "identity", None)
        if ident is not None:
            p["identity"] = int(ident)
        graphics = getattr(body, "graphics", None)  # Overlay: use its first graphic's point
        if graphics:
            g = graphics[0]
            p["lat"], p["lon"] = g.location.lat, g.location.lon
            p["description"] = getattr(body, "name", "overlay")
        # the three extended types carry fields the flat schema keys differently
        if isinstance(body, GeneralInfo):
            p["description"] = body.subject  # inject maps description → GenInfo.subject
        elif isinstance(body, Receipt):
            p["ack_message_id"] = body.ack_message_id
            p["description"] = body.note
        elif isinstance(body, Chatrooms):
            p["text"] = ",".join(r.name or r.room_id for r in body.rooms)  # comma-separated rooms
        return p

    def _status(self, text: str) -> None:
        self._on_event({"kind": "status", "text": text})


class _RxHandler:
    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, engine: SimulatorEngine) -> None:
        self._engine = engine

    async def handle(self, message: JdssMessage) -> None:
        h = message.header
        await self._engine._receive(
            h.originator_id, h.message_id, str(message.type),
            getattr(message.body, "callsign", None),
        )
