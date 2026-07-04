"""JdssGateway — the composition root.

This is the *only* module that turns configuration into concrete objects. It reads a
:class:`GatewayConfig`, resolves each pluggable choice through the registry, wires them
together via constructor injection, and exposes a small application-facing API. Every other
module depends purely on the protocols in :mod:`jdssarrow.interfaces`; swapping any
implementation is a config edit, not a code change (Open/Closed + Dependency Inversion).

Volume wiring:

* **Vol V**  ``AddressAllocator`` picks the multicast group/port for the network.
* **Vol IV** ``Transport`` (built for that address) carries frames; ``ExchangeEngine`` adds
  reliability + dispatch.
* **Vol III** ``RadioBearer`` (the loaned radio) constructs the transport for a network.
* **Vol I**  ``SecurityProvider`` protects/verifies frames.
* **Vol II** ``Codec`` serializes the JDSSDM messages.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable

from jdssarrow.capabilities import CapabilityError, CapabilityMatrix
from jdssarrow.config.models import GatewayConfig
from jdssarrow.connections.distributor import PolicyDistributor
from jdssarrow.connections.policy import CompositePolicy, MatrixConnectionPolicy
from jdssarrow.connections.signing import AuthoritySigner, AuthorityVerifier
from jdssarrow.datamodel.messages import JdssMessage, MessageHeader
from jdssarrow.iem.exchange import ExchangeEngine
from jdssarrow.interfaces import (
    AddressAllocator,
    Codec,
    ConnectionPolicy,
    MessageHandler,
    RadioBearer,
    SecurityProvider,
    Transport,
)
from jdssarrow.monitor.gossip import PeerGossip
from jdssarrow.monitor.metrics import GatewayMetrics
from jdssarrow.plugins.registry import Registry
from jdssarrow.plugins.registry import registry as default_registry

_log = logging.getLogger("jdssarrow.gateway")


class JdssGateway:
    def __init__(
        self,
        config: GatewayConfig,
        registry: Registry | None = None,
    ) -> None:
        self.config = config
        self._registry = registry or default_registry
        self.metrics = GatewayMetrics(
            node_id=config.identity.node_id,
            telemetry_capacity=config.web.telemetry_capacity,
        )

        # Vol V — Network Access: resolve the multicast endpoint for this network.
        self.allocator: AddressAllocator = self._registry.create(
            "allocators", config.plugins.allocator
        )
        self._group, self._port = self._resolve_endpoint()

        # Vol I — Security.
        self.security: SecurityProvider = self._build_security()

        # Vol II — Codec.
        self.codec: Codec = self._registry.create("codecs", config.plugins.codec)

        # Connection management: this node's local admit/deny policy (its matrix row) plus a
        # coalition-wide policy distributed by the authority. Effective = both must allow.
        self.policy: ConnectionPolicy = self._build_policy()
        self.coalition: MatrixConnectionPolicy = self._build_coalition_policy()
        self._effective_policy: ConnectionPolicy = CompositePolicy(self.policy, self.coalition)
        self._distributor: PolicyDistributor | None = None

        # Capability matrix: which message types this node may receive / emit.
        self.capabilities = CapabilityMatrix(
            receive=config.capabilities.receive, emit=config.capabilities.emit
        )

        # Vol III — Loaned Radio: the bearer constructs the transport for the network.
        self.bearer: RadioBearer = self._registry.create(
            "bearers",
            config.plugins.bearer,
            config.identity.node_id,
            self._transport_factory(),
        )

        self._engine: ExchangeEngine | None = None
        # Handlers may be registered before start(); they are applied when the engine exists.
        self._pending_handlers: list[MessageHandler] = []
        self._started_at: float | None = None
        self._gossip: PeerGossip | None = None

    # ------------------------------------------------------------- construction
    def _resolve_endpoint(self) -> tuple[str, int]:
        net = self.config.network
        if net.multicast_group and net.multicast_port:
            return net.multicast_group, net.multicast_port
        return self.allocator.multicast_group(net.network_id)

    def _build_policy(self) -> ConnectionPolicy:
        c = self.config.connections
        if c.policy == "allow_all":
            return self._registry.create("policies", "allow_all")
        overrides = {p: False for p in c.blocked}
        overrides.update({p: True for p in c.allowed})
        return self._registry.create(
            "policies",
            "matrix",
            node_id=self.config.identity.node_id,
            default_action=c.default_action,
            overrides=overrides,
        )

    def _build_coalition_policy(self) -> MatrixConnectionPolicy:
        c = self.config.connections
        is_authority = (
            c.policy_authority is not None and self.config.identity.node_id == c.policy_authority
        )
        if is_authority:
            overrides = {p: False for p in c.coalition_blocked}
            overrides.update({p: True for p in c.coalition_allowed})
            return MatrixConnectionPolicy(
                node_id="COALITION", default_action=c.coalition_default_action, overrides=overrides
            )
        # non-authority nodes start permissive and get the real policy via gossip
        return MatrixConnectionPolicy(node_id="COALITION", default_action="allow")

    def _build_security(self) -> SecurityProvider:
        name = self.config.plugins.security
        if name == "psk":
            return self._registry.create("security", name, self.config.network.psk)
        return self._registry.create("security", name)

    def _transport_factory(self) -> Callable[[str], Transport]:
        """Return a factory the bearer uses to build the transport for a network."""
        name = self.config.plugins.transport
        group, port = self._group, self._port
        drop_hook = self.metrics.record_dropped
        registry = self._registry

        def factory(network_id: str) -> Transport:
            if name == "udp":
                return registry.create(
                    "transports", name, group=group, port=port, drop_hook=drop_hook
                )
            if name == "loopback":
                return registry.create("transports", name, group=group)
            # Unknown/third-party transport: best-effort construct with no args.
            return registry.create("transports", name)

        return factory

    # --------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        await self.bearer.join(self.config.network.network_id)
        self._engine = ExchangeEngine(
            node_id=self.config.identity.node_id,
            transport=self.bearer.transport(),
            codec=self.codec,
            security=self.security,
            metrics=self.metrics,
            policy=self._effective_policy,
            capabilities=self.capabilities,
            repeat=self.config.network.repeat,
        )
        for handler in self._pending_handlers:
            self._engine.add_handler(handler)

        # Vol-monitoring: peer-digest gossip so every node can build the live connection matrix.
        if self.config.gossip.enabled:
            self._gossip = PeerGossip(
                self.config.identity.node_id,
                self._engine,
                self.metrics,
                interval_s=self.config.gossip.interval_s,
                peer_timeout_s=self.config.web.peer_timeout_s,
            )
            self._gossip.attach()

        # Coalition-wide policy distribution over the control channel.
        conn = self.config.connections
        if conn.policy_authority is not None:
            is_authority = self.config.identity.node_id == conn.policy_authority
            # per-authority Ed25519 signing: authority signs, everyone verifies with the pubkey
            signer = (
                AuthoritySigner(conn.authority_private_key)
                if is_authority and conn.authority_private_key
                else None
            )
            verifier = (
                AuthorityVerifier(conn.authority_public_key)
                if conn.authority_public_key
                else None
            )
            self._distributor = PolicyDistributor(
                self.config.identity.node_id,
                self._engine,
                self.coalition,
                authority_id=conn.policy_authority,
                interval_s=self.config.gossip.interval_s,
                signer=signer,
                verifier=verifier,
            )
            self._distributor.attach()

        await self._engine.start()
        if self._gossip is not None:
            await self._gossip.start()
        if self._distributor is not None:
            await self._distributor.start()
        self._started_at = time.time()
        _log.info(
            "gateway '%s' started on %s:%s (codec=%s transport=%s security=%s)",
            self.config.identity.node_id, self._group, self._port,
            self.config.plugins.codec, self.config.plugins.transport, self.config.plugins.security,
        )

    async def stop(self) -> None:
        if self._distributor is not None:
            await self._distributor.stop()
            self._distributor = None
        if self._gossip is not None:
            await self._gossip.stop()
            self._gossip = None
        if self._engine is not None:
            await self._engine.stop()
            self._engine = None
        await self.bearer.leave()
        self._started_at = None

    # --------------------------------------------------------------- app-facing
    @property
    def engine(self) -> ExchangeEngine:
        if self._engine is None:
            raise RuntimeError("gateway not started")
        return self._engine

    def add_handler(self, handler: MessageHandler) -> None:
        if self._engine is None:
            self._pending_handlers.append(handler)
        else:
            self._engine.add_handler(handler)

    def _header(self) -> MessageHeader:
        return MessageHeader(
            originator_id=self.config.identity.node_id,
            network_id=self.config.network.network_id,
            classification=self.config.classification.level,
            releasable_to=self.config.classification.releasable_to,
        )

    async def publish(self, body: object) -> JdssMessage:
        """Wrap a message body in a header and transmit it."""
        message = JdssMessage(header=self._header(), body=body)  # type: ignore[arg-type]
        if not self.capabilities.can_emit(message.type):
            raise CapabilityError(f"emitting {message.type} is disabled on this node")
        return await self.engine.publish(message)

    async def ingest_from_bridge(self, message: JdssMessage) -> None:
        """Inject a message produced by a CoT/ATAK bridge under *its own* originator.

        Broadcasts it to the coalition **and** folds it into this node's own picture and local
        relays (so a bridged EUD/server shows up in this node's peers/matrix and is fanned out to
        the other bridges). Unlike :meth:`publish`, the originator is preserved as-is (not this
        node's identity), so each bridged source is a distinct coalition peer."""
        await self.engine.publish(message)
        await self.engine.deliver_local(message)

    # ------------------------------------------------------------ capabilities
    def capabilities_snapshot(self) -> dict:
        return self.capabilities.snapshot()

    def set_capability(self, message_type: str, direction: str, allowed: bool) -> dict:
        self.capabilities.set(message_type, direction, allowed)
        return self.capabilities.snapshot()

    @property
    def endpoint(self) -> tuple[str, int]:
        return self._group, self._port

    @property
    def running(self) -> bool:
        return self._engine is not None

    @property
    def uptime_s(self) -> float:
        return 0.0 if self._started_at is None else time.time() - self._started_at

    def peers(self) -> list[dict]:
        """Connected/known peers with identity + freshness (for the monitor UI)."""
        timeout = self.config.web.peer_timeout_s
        return self.metrics.peers(timeout_s=timeout)

    # ------------------------------------------------------------------ logging
    def message_log(
        self, limit: int = 200, direction: str | None = None, disposition: str | None = None
    ) -> list[dict]:
        """Recent message audit entries (incoming/outgoing, accepted/rejected + reason)."""
        return self.metrics.audit.recent(limit, direction=direction, disposition=disposition)

    def application_log(self, limit: int = 200, min_level: str | None = None) -> list[dict]:
        """Recent application log records (lifecycle, warnings, errors)."""
        from jdssarrow.audit import app_log

        return app_log(limit, min_level=min_level)

    def connection_matrix(self) -> dict:
        """Live N×N connection matrix assembled from peer-digest gossip.

        With gossip enabled this reflects the whole coalition network (our row + every remote
        row we've received). Without it, only our own row is known.
        """
        if self._gossip is not None:
            matrix = self._gossip.matrix()
        else:
            node_id = self.config.identity.node_id
            own = {p["node_id"]: p["messages"] for p in self.peers()}
            matrix = {
                "nodes": sorted({node_id} | set(own)),
                "rows": {node_id: own},
                "rogue_node": None,
            }
        # attach local + coalition policy so the UI can mark blocked cells
        matrix["policy"] = self.connection_policy()
        matrix["coalition"] = self.coalition_snapshot()
        return matrix

    # --------------------------------------------------------- connection mgmt
    def connection_policy(self) -> dict:
        snap = self.policy.snapshot() if hasattr(self.policy, "snapshot") else {}
        return {"node_id": self.config.identity.node_id, **snap}

    def block_peer(self, peer_id: str) -> dict:
        if hasattr(self.policy, "block"):
            self.policy.block(peer_id)
        return self.connection_policy()

    def allow_peer(self, peer_id: str) -> dict:
        if hasattr(self.policy, "allow"):
            self.policy.allow(peer_id)
        return self.connection_policy()

    def reset_peer(self, peer_id: str) -> dict:
        if hasattr(self.policy, "reset"):
            self.policy.reset(peer_id)
        return self.connection_policy()

    # ---------------------------------------------------- coalition-wide policy
    def coalition_snapshot(self) -> dict:
        snap = self.coalition.snapshot()
        d = self._distributor
        cfg = self.config.connections
        is_authority = (
            cfg.policy_authority is not None
            and self.config.identity.node_id == cfg.policy_authority
        )
        return {
            **snap,
            "authority_id": cfg.policy_authority,
            "am_authority": is_authority,
            "version": d.version if d else (1 if is_authority else 0),
            "enabled": cfg.policy_authority is not None,
            "signed": bool(d.signed) if d else (cfg.authority_public_key is not None),
        }

    async def coalition_set(self, peer_id: str, action: str) -> dict:
        """Authority-only: change the coalition policy and distribute it to all nodes."""
        if self._distributor is None:
            raise PermissionError("coalition policy distribution is not enabled")
        if action == "block":
            await self._distributor.block(peer_id)
        elif action == "allow":
            await self._distributor.allow(peer_id)
        elif action == "reset":
            await self._distributor.reset(peer_id)
        else:
            raise ValueError("action must be allow, block or reset")
        return self.coalition_snapshot()

    def health(self) -> dict:
        """System/monitor/thread health for the dashboard health panel."""
        try:
            tasks = len(asyncio.all_tasks())
        except RuntimeError:  # no running loop (e.g. sync test context)
            tasks = 0
        return {
            "status": "ok" if self.running else "stopped",
            "node_id": self.config.identity.node_id,
            "uptime_s": round(self.uptime_s, 1),
            "engine_running": self.running,
            "transport": self.config.plugins.transport,
            "codec": self.config.plugins.codec,
            "security": self.config.plugins.security,
            "repeat": self.config.network.repeat,
            "threads": threading.active_count(),
            "asyncio_tasks": tasks,
            "telemetry_buffered": len(self.metrics.telemetry),
            "ws_subscribers": self.metrics.subscriber_count(),
            "dropped_rejected": self.metrics.drops().get("decode", 0),
            "dropped_by_policy": self.metrics.drops().get("policy", 0),
            "dropped_by_capability": self.metrics.drops().get("capability", 0),
            "policy": self.policy.name,
            "policy_authority": self.config.connections.policy_authority,
            "is_policy_authority": bool(self._distributor and self._distributor.is_authority),
            "coalition_version": self._distributor.version if self._distributor else 0,
            "gossip_remote_rows": self._gossip.remote_count() if self._gossip else 0,
            "classification": self.config.classification.level,
            "releasable_to": self.config.classification.releasable_to,
        }
