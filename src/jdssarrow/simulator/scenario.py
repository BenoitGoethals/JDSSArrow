"""Scenario runner: spawn a roster of JDSS clients, drive them, report compliance.

The :class:`Simulation` builds one :class:`~jdssarrow.gateway.gateway.JdssGateway` per client,
joins them all to a single coalition network, and steps their behaviours over a number of
ticks. The command-post client acts as the network monitor and records the common operational
picture, from which a :class:`SimReport` summarises what was exchanged and whether the traffic
was JDSS-conformant end to end.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass, field
from math import cos, sin

from jdssarrow.config.models import (
    ClassificationConfig,
    GatewayConfig,
    NetworkConfig,
    NodeIdentity,
    PluginSelection,
)
from jdssarrow.datamodel.messages import JdssMessage, MessageType
from jdssarrow.datamodel.symbology import StandardIdentity
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode
from jdssarrow.plugins.registry import PluginError, Registry
from jdssarrow.plugins.registry import registry as default_registry
from jdssarrow.simulator.profiles import CLIENT_PROFILES, ClientProfile
from jdssarrow.simulator.rogue import RogueClient

_COALITION_PSK = "exercise-key"

#: default order of battle: role → count. 9 compliant clients on one network.
DEFAULT_ROSTER: list[tuple[str, int]] = [
    ("commandpost", 1),
    ("teamleader", 1),
    ("rifleman", 2),
    ("medic", 1),
    ("scout", 1),
    ("observer", 1),
    ("sensor", 1),
    ("atak", 1),  # ATAK end-user device
    ("vehicle", 1),  # mounted C2 platform
]

_ALL_TYPES = {str(t) for t in MessageType}


class Stats:
    """Network-wide tallies recorded by the command post (the COP sink)."""

    def __init__(self) -> None:
        self.casevac_requests = 0
        self.casevac_acks = 0
        self.observed: dict[str, int] = {}
        self.per_origin: dict[str, int] = {}
        self.origins: set[str] = set()

    def observe(self, message: JdssMessage) -> None:
        t = message.type
        self.observed[t] = self.observed.get(t, 0) + 1
        origin = message.header.originator_id
        self.origins.add(origin)
        self.per_origin[origin] = self.per_origin.get(origin, 0) + 1


class _ProfileHandler:
    """Bridges received messages to a client's profile reaction."""

    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, client: SimClient) -> None:
        self._client = client

    async def handle(self, message: JdssMessage) -> None:
        await self._client.profile.on_message(self._client, message)


class SimClient:
    """One JDSS-compliant client: a role behaviour driving a real gateway/node."""

    def __init__(
        self,
        node_id: str,
        callsign: str,
        profile: ClientProfile,
        gateway: JdssGateway,
        home: tuple[float, float],
        stats: Stats,
    ) -> None:
        self.node_id = node_id
        self.callsign = callsign
        self.profile = profile
        self.gateway = gateway
        self.node = SoldierNode(gateway)
        self.pos = home
        self.stats = stats
        self.sent: dict[str, int] = {}
        self.node.add_handler(_ProfileHandler(self))

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.node.start()
        await self.profile.on_start(self)

    async def stop(self) -> None:
        await self.node.stop()

    async def step(self, tick: int) -> None:
        await self.profile.on_tick(self, tick)

    def _count(self, type_name: str) -> None:
        self.sent[type_name] = self.sent.get(type_name, 0) + 1

    def _walk(self) -> tuple[float, float]:
        # deterministic drift so presence tracks a moving soldier
        lat, lon = self.pos
        self.pos = (lat + 0.0002, lon + 0.0001)
        return self.pos

    # ------------------------------------------------------------ message verbs
    async def identify(self) -> None:
        await self.node.identify()
        self._count(str(MessageType.IDENTIFICATION))

    async def presence(self, battery_pct: int | None = 95) -> None:
        lat, lon = self._walk()
        await self.node.presence(lat, lon, battery_pct=battery_pct)
        self._count(str(MessageType.PRESENCE))

    async def chat(self, text: str, recipient: str = "all") -> None:
        await self.node.chat(text, recipient=recipient)
        self._count(str(MessageType.CHAT))

    async def contact(
        self, description: str, identity: StandardIdentity = StandardIdentity.HOSTILE
    ) -> None:
        lat, lon = self.pos
        await self.node.report_contact(lat, lon, description=description, identity=identity)
        self._count(str(MessageType.CONTACT))

    async def casevac(self, urgent: int = 1, priority: int = 0) -> None:
        self.stats.casevac_requests += 1
        lat, lon = self.pos
        await self.node.request_casevac(lat, lon, urgent=urgent, priority=priority)
        self._count(str(MessageType.CASEVAC))

    async def move_to(self, lat: float, lon: float) -> None:
        self.pos = (lat, lon)
        await self.presence()

    async def publish(self, body: object, type_name: str) -> None:
        """Emit a typed JDSSDM body (used for Sketch/Overlay that have no node helper)."""
        await self.gateway.publish(body)
        self._count(type_name)


@dataclass
class SimReport:
    network_id: str
    transport: str
    codec: str
    ticks: int
    clients: list[dict] = field(default_factory=list)
    types_observed: list[str] = field(default_factory=list)
    casevac_requests: int = 0
    casevac_acks: int = 0
    peers_at_command_post: list[str] = field(default_factory=list)
    rogue_mode: str | None = None
    rogue_frames_sent: int = 0
    rogue_frames_rejected: int = 0
    rogue_observed: bool = False
    #: connectivity matrix — ordered node ids, and observer → {originator: messages heard}.
    nodes: list[str] = field(default_factory=list)
    matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    rogue_node: str | None = None

    @property
    def all_message_types(self) -> bool:
        """True if every JDSSDM message type was observed on the network."""
        return _ALL_TYPES.issubset(set(self.types_observed))

    @property
    def rogue_rejected(self) -> bool:
        """True if a rogue was present and the network rejected all of its traffic."""
        return (
            self.rogue_mode is not None
            and not self.rogue_observed
            and self.rogue_frames_rejected > 0
        )

    def format(self) -> str:
        lines = [
            f"JDSS simulation — network '{self.network_id}' "
            f"({self.codec}/{self.transport}), {self.ticks} ticks",
            f"  clients: {len(self.clients)}",
        ]
        for c in sorted(self.clients, key=lambda c: c["role"]):
            by_type = ", ".join(f"{k}={v}" for k, v in sorted(c["sent_by_type"].items()))
            lines.append(
                f"    {c['callsign']:<12} {c['role']:<16} {c['nation']}  "
                f"sent {c['sent_total']:<3} [{by_type}]"
            )
        coverage = "✓ ALL" if self.all_message_types else "✗"
        types_csv = ", ".join(sorted(self.types_observed))
        lines += [
            f"  message types observed network-wide: {len(self.types_observed)}/{len(_ALL_TYPES)} "
            f"{coverage} — {types_csv}",
            f"  CASEVAC: {self.casevac_requests} requested / "
            f"{self.casevac_acks} acknowledged by medic",
            f"  peers seen at command post: {len(self.peers_at_command_post)} "
            f"({', '.join(sorted(self.peers_at_command_post))})",
        ]
        if self.rogue_mode is not None:
            verdict = "REJECTED ✓" if self.rogue_rejected else "LEAKED ✗"
            lines.append(
                f"  rogue client ('{self.rogue_mode}'): {self.rogue_frames_sent} frames injected, "
                f"{self.rogue_frames_rejected} dropped by receivers, "
                f"in COP={self.rogue_observed} → {verdict}"
            )
        return "\n".join(lines)

    def format_matrix(self) -> str:
        """ASCII connection matrix: rows = observer, columns = originator heard-from.

        Cell = number of messages that row's node accepted from that column's node. ``–`` on
        the diagonal (a node never hears itself). A column of all-zeros is a node nobody
        accepts — exactly what a rejected rogue looks like.
        """
        if not self.nodes:
            return "(no matrix)"
        w = 11

        def short(n: str) -> str:
            return n[: w - 1]

        head = "observer \\ from".ljust(16) + "".join(short(n).rjust(w) for n in self.nodes)
        lines = [head]
        for obs in self.nodes:
            row = self.matrix.get(obs, {})
            cells = "".join(
                ("–" if n == obs else str(row.get(n, 0))).rjust(w) for n in self.nodes
            )
            marker = "  <- rogue" if obs == self.rogue_node else ""
            lines.append(short(obs).ljust(16) + cells + marker)
        if self.rogue_node is not None:
            lines.append(f"(rogue '{self.rogue_node}' column is all-zero ⇒ nobody accepts it)")
        return "\n".join(lines)


def _make_profile(registry: Registry, name: str) -> ClientProfile:
    """Instantiate a profile via the plugin registry, falling back to the built-ins.

    The registry (entry-point discovery) is the pluggable path; the built-in map keeps the
    simulator working even in an environment where entry points were not installed.
    """
    try:
        return registry.create("profiles", name)
    except PluginError:
        return CLIENT_PROFILES[name]()


class Simulation:
    def __init__(
        self,
        roster: list[tuple[str, int]] | None = None,
        *,
        network_id: str = "exercise-jdss",
        transport: str = "loopback",
        codec: str = "xml",
        security: str = "psk",
        base: tuple[float, float] = (50.8503, 4.3517),
        classification_level: int = 1,
        rogue: str | None = None,
        blocks: dict[str, list[str]] | None = None,
        psk: str = _COALITION_PSK,
        registry: Registry | None = None,
    ) -> None:
        self.roster = roster or DEFAULT_ROSTER
        self.network_id = network_id
        self.transport = transport
        self.codec = codec
        self.security = security
        self.base = base
        self.classification_level = classification_level
        self._psk = psk
        self.rogue_mode = rogue
        #: node_id -> peers it refuses (connection-matrix management demo)
        self.blocks = blocks or {}
        self._registry = registry or default_registry
        self.stats = Stats()
        self.clients: list[SimClient] = []
        self.rogue: RogueClient | None = None
        self._build()
        if rogue:
            self._build_rogue(rogue)

    # --------------------------------------------------------------- build
    def _config(self, node_id: str, callsign: str, role: str, nation: str) -> GatewayConfig:
        return GatewayConfig(
            identity=NodeIdentity(
                node_id=node_id, callsign=callsign, unit="JDSS-EXER", nation=nation, role=role
            ),
            plugins=PluginSelection(
                transport=self.transport, codec=self.codec, security=self.security
            ),
            network=NetworkConfig(network_id=self.network_id, repeat=2, psk=self._psk),
            classification=ClassificationConfig(
                level=self.classification_level, releasable_to="REL BEL NLD"
            ),
        )

    def _build(self) -> None:
        idx = 0
        for profile_name, count in self.roster:
            for n in range(count):
                profile = _make_profile(self._registry, profile_name)
                node_id = f"{profile_name}-{n + 1}"
                callsign = f"{profile_name[:3].upper()}-{n + 1}"
                nation = "BEL" if idx % 2 == 0 else "NLD"
                config = self._config(node_id, callsign, profile.role, nation)
                gateway = JdssGateway(config, self._registry)
                # spread clients on a ~1km circle around the base
                angle = idx * 0.9
                home = (self.base[0] + 0.004 * sin(angle), self.base[1] + 0.004 * cos(angle))
                for blocked in self.blocks.get(node_id, []):
                    gateway.block_peer(blocked)  # apply connection-matrix policy
                self.clients.append(
                    SimClient(node_id, callsign, profile, gateway, home, self.stats)
                )
                idx += 1

    def _build_rogue(self, mode: str) -> None:
        # wrong_key uses an unauthorised key; other modes sit on the correct (leaked) key.
        psk = "rogue-unauthorised-key" if mode == "wrong_key" else _COALITION_PSK
        config = GatewayConfig(
            identity=NodeIdentity(
                node_id="rogue-1", callsign="RGUE-1", unit="???", nation="ZZ", role="rogue"
            ),
            plugins=PluginSelection(transport=self.transport, codec=self.codec, security="psk"),
            network=NetworkConfig(network_id=self.network_id, repeat=2, psk=psk),
            classification=ClassificationConfig(level=self.classification_level),
        )
        gateway = JdssGateway(config, self._registry)
        self.rogue = RogueClient(gateway, mode, coalition_psk=_COALITION_PSK)

    # ------------------------------------------------------ lifecycle primitives
    async def start(self) -> None:
        """Start all clients (and the rogue) — used by the open-ended background runner."""
        for client in self.clients:
            await client.start()
        if self.rogue is not None:
            await self.rogue.start()

    async def tick(self, n: int) -> None:
        """Advance every client (and the rogue) by one behaviour tick."""
        for client in self.clients:
            await client.step(n)
        if self.rogue is not None:
            await self.rogue.step(n)

    async def stop(self) -> None:
        """Stop all clients (and the rogue)."""
        for client in self.clients:
            await client.stop()
        if self.rogue is not None:
            await self.rogue.stop()

    # --------------------------------------------------------------- run
    async def run(self, ticks: int = 20, tick_interval: float = 0.02) -> SimReport:
        await self.start()
        try:
            for n in range(ticks):
                await self.tick(n)
                await asyncio.sleep(tick_interval)
            # let the last in-flight reactions (e.g. medic ack) settle
            await asyncio.sleep(tick_interval * 2)
        finally:
            await self.stop()
        return self._report(ticks)

    def connection_matrix(self) -> tuple[list[str], dict[str, dict[str, int]], str | None]:
        """Assemble the N×N connectivity matrix from every node's own peer view.

        Each node (incl. the rogue) independently records who it accepted traffic from, so its
        ``metrics.peers()`` is one row of the matrix. A very large timeout is used so the row
        reflects the whole run rather than only recently-seen peers.
        """
        observers: list[SimClient | RogueClient] = list(self.clients)
        rogue_node = None
        if self.rogue is not None:
            rogue_node = self.rogue.node_id
            observers.append(self.rogue)
        nodes = [o.node_id for o in observers]
        rows: dict[str, dict[str, int]] = {}
        for obs in observers:
            heard = obs.gateway.metrics.peers(timeout_s=10_000_000)
            rows[obs.node_id] = {p["node_id"]: p["messages"] for p in heard}
        return nodes, rows, rogue_node

    def _report(self, ticks: int) -> SimReport:
        nodes, matrix, rogue_node = self.connection_matrix()
        return SimReport(
            network_id=self.network_id,
            transport=self.transport,
            codec=self.codec,
            ticks=ticks,
            clients=[
                {
                    "node_id": c.node_id,
                    "callsign": c.callsign,
                    "role": c.profile.role,
                    "device": c.profile.device,
                    "nation": c.gateway.config.identity.nation,
                    "sent_total": sum(c.sent.values()),
                    "sent_by_type": dict(c.sent),
                }
                for c in self.clients
            ],
            types_observed=sorted(self.stats.observed),
            casevac_requests=self.stats.casevac_requests,
            casevac_acks=self.stats.casevac_acks,
            peers_at_command_post=sorted(self.stats.origins),
            rogue_mode=self.rogue_mode,
            rogue_frames_sent=self.rogue.frames_sent if self.rogue else 0,
            # frames the legitimate receivers dropped for failed auth / framing / schema
            rogue_frames_rejected=sum(
                c.gateway.metrics.drops().get("decode", 0) for c in self.clients
            ),
            rogue_observed=self.rogue is not None and self.rogue.node_id in self.stats.origins,
            nodes=nodes,
            matrix=matrix,
            rogue_node=rogue_node,
        )
