"""Gateway metrics sink: Prometheus counters + live event fan-out + node presence.

``GatewayMetrics`` implements the :class:`MetricsSink` protocol. It is the single place the
gateway reports observability events to, and it serves three consumers:

* **Prometheus** — counters/gauges exposed at ``/metrics`` for scraping.
* **Arrow telemetry** — every message is appended to the ring buffer for history queries.
* **Live UI** — a set of ``asyncio.Queue`` subscribers receives compact JSON events that the
  web layer streams to the React dashboard over WebSocket.

The sink stays synchronous (the protocol demands it); live delivery is done with
``put_nowait`` so a slow UI can never block the exchange hot path — it just drops events.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest

from jdssarrow.audit import MessageLog
from jdssarrow.datamodel.messages import JdssMessage
from jdssarrow.monitor.telemetry_arrow import ArrowTelemetryBuffer

# framing/security/codec failures collapse to one "decode" counter label (stable metrics);
# the audit log still keeps the detailed reason.
_COARSE_DROP = {"framing": "decode", "security": "decode", "codec": "decode"}


class GatewayMetrics:
    name = "gateway"

    def __init__(self, node_id: str, telemetry_capacity: int = 1000) -> None:
        self._node_id = node_id
        self.telemetry = ArrowTelemetryBuffer(capacity=telemetry_capacity)
        self.audit = MessageLog(capacity=max(1000, telemetry_capacity))
        # node_id -> metadata (last_seen, callsign, nation, role, count, classification…)
        self._nodes: dict[str, dict[str, Any]] = {}
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._max_classification_seen = 0
        self._drops: dict[str, int] = {}  # coarse reason -> count (readable for tests/UI)

        # Prometheus — private registry so multiple gateways in one process don't clash.
        self.registry = CollectorRegistry()
        self._sent = Counter(
            "jdss_messages_sent_total", "Messages sent", ["type"], registry=self.registry
        )
        self._received = Counter(
            "jdss_messages_received_total",
            "Messages received",
            ["type"],
            registry=self.registry,
        )
        self._dropped = Counter(
            "jdss_messages_dropped_total",
            "Messages dropped",
            ["reason"],
            registry=self.registry,
        )
        self._known_nodes = Gauge(
            "jdss_known_nodes", "Distinct peers seen", registry=self.registry
        )

    # ------------------------------------------------------------ MetricsSink API
    def record_sent(self, message: JdssMessage) -> None:
        self._sent.labels(type=message.type).inc()
        self.telemetry.add(message)
        self.audit.record(
            "out",
            "accepted",
            type=message.type,
            originator_id=message.header.originator_id,
            message_id=message.header.message_id,
        )
        self._emit("sent", message)

    def record_received(self, message: JdssMessage) -> None:
        self._received.labels(type=message.type).inc()
        self.telemetry.add(message)
        self._enrich_peer(message)
        self.audit.record(
            "in",
            "accepted",
            type=message.type,
            originator_id=message.header.originator_id,
            message_id=message.header.message_id,
        )
        self._emit("received", message)

    def record_dropped(self, reason: str, message: JdssMessage | None = None) -> None:
        # coarse counter label (framing/security/codec collapse to "decode" for stable metrics)
        first = reason.split(":", 1)[0]
        label = _COARSE_DROP.get(first, first)
        self._dropped.labels(reason=label).inc()
        self._drops[label] = self._drops.get(label, 0) + 1
        # audit log keeps the *detailed* reason and the message identity when known
        self.audit.record(
            "in",
            "rejected",
            reason=reason,
            type=message.type if message is not None else None,
            originator_id=message.header.originator_id if message is not None else None,
            message_id=message.header.message_id if message is not None else None,
        )

    def drops(self) -> dict[str, int]:
        """Readable drop counts by reason. ``decode`` = failed auth/framing/schema;
        ``duplicate`` = deduplicated message repetition."""
        return dict(self._drops)

    def node_seen(self, node_id: str) -> None:
        rec = self._nodes.setdefault(node_id, {"count": 0})
        rec["last_seen"] = time.time()
        self._known_nodes.set(len(self._nodes))

    def _enrich_peer(self, message: JdssMessage) -> None:
        """Learn a peer's identity + classification from the messages it sends."""
        h = message.header
        rec = self._nodes.setdefault(h.originator_id, {"count": 0})
        rec["last_seen"] = time.time()
        rec["count"] = rec.get("count", 0) + 1
        rec["classification"] = int(h.classification)
        rec["releasable_to"] = h.releasable_to
        self._max_classification_seen = max(self._max_classification_seen, int(h.classification))
        body = message.body
        # Identification/Presence reveal the callsign; Identification adds unit/nation/role.
        for attr in ("callsign", "unit", "nation", "role"):
            value = getattr(body, attr, None)
            if value:
                rec[attr] = value
        self._known_nodes.set(len(self._nodes))

    # ------------------------------------------------------------------ queries
    def nodes(self) -> dict[str, dict[str, Any]]:
        return {k: dict(v) for k, v in self._nodes.items()}

    def peers(self, timeout_s: int = 15) -> list[dict[str, Any]]:
        """Known peers with freshness; ``connected`` if seen within ``timeout_s``."""
        now = time.time()
        out: list[dict[str, Any]] = []
        for node_id, rec in self._nodes.items():
            last = rec.get("last_seen", 0.0)
            age = now - last
            out.append(
                {
                    "node_id": node_id,
                    "callsign": rec.get("callsign"),
                    "unit": rec.get("unit"),
                    "nation": rec.get("nation"),
                    "role": rec.get("role"),
                    "messages": rec.get("count", 0),
                    "classification": rec.get("classification", 0),
                    "releasable_to": rec.get("releasable_to", "ALL"),
                    "seconds_ago": round(age, 1),
                    "connected": age <= timeout_s,
                }
            )
        return sorted(out, key=lambda p: p["seconds_ago"])

    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def prometheus(self) -> bytes:
        return generate_latest(self.registry)

    def snapshot(self) -> dict[str, Any]:
        return {
            "node_id": self._node_id,
            "known_nodes": list(self._nodes),
            "counts_by_type": self.telemetry.count_by_type(),
            "buffered": len(self.telemetry),
            "max_classification_seen": self._max_classification_seen,
            "dropped_by_reason": dict(self._drops),
        }

    # ------------------------------------------------------- live event fan-out
    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(q)

    def _emit(self, direction: str, message: JdssMessage) -> None:
        h = message.header
        # best-effort callsign for the feed's "from who" column
        callsign = self._nodes.get(h.originator_id, {}).get("callsign") or getattr(
            message.body, "callsign", None
        )
        event = {
            "direction": direction,
            "type": message.type,
            "originator_id": h.originator_id,
            "callsign": callsign,
            "message_id": h.message_id,
            "reporting_time": h.reporting_time.isoformat(),
            "classification": int(h.classification),
            "releasable_to": h.releasable_to,
            "body": message.body.model_dump(mode="json"),
        }
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: drop rather than block
                pass
