"""Peer-digest gossip → a live, cross-node connection matrix.

A single node only ever knows *its own* row of the connection matrix (who it has heard from).
To assemble the full N×N matrix from live traffic, every node periodically broadcasts its row
as an out-of-band control message; each node collects the rows it receives. Because the
digests ride the same HMAC-protected control channel, an unauthorised node's digest is dropped
just like its data — so a rejected rogue never appears as a column.

Digests are best-effort and carry a timestamp; stale remote rows (a node that went quiet) age
out of the matrix, so the picture reflects current connectivity.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

from jdssarrow.iem.exchange import ExchangeEngine
from jdssarrow.monitor.metrics import GatewayMetrics

_KIND = "peerdigest"


class PeerGossip:
    def __init__(
        self,
        node_id: str,
        engine: ExchangeEngine,
        metrics: GatewayMetrics,
        interval_s: float = 2.0,
        peer_timeout_s: int = 15,
    ) -> None:
        self._node_id = node_id
        self._engine = engine
        self._metrics = metrics
        self._interval = max(0.01, interval_s)
        self._peer_timeout = peer_timeout_s
        #: remote node_id -> {"row": {originator: count}, "ts": epoch}
        self._remote: dict[str, dict] = {}
        #: a remote row is considered current for this long after its last digest.
        self._ttl = max(10.0, self._interval * 4)
        self._task: asyncio.Task | None = None

    def attach(self) -> None:
        self._engine.on_control(_KIND, self._on_digest)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ------------------------------------------------------------------ internals
    def _own_row(self) -> dict[str, int]:
        return {p["node_id"]: p["messages"] for p in self._metrics.peers(self._peer_timeout)}

    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._engine.publish_control(_KIND, {"row": self._own_row()})
            await asyncio.sleep(self._interval)

    async def _on_digest(self, data: dict) -> None:
        node = data.get("node")
        if node and node != self._node_id:
            self._remote[node] = {"row": data.get("row", {}), "ts": time.time()}

    # ------------------------------------------------------------------ query
    def remote_count(self) -> int:
        return len(self._remote)

    def matrix(self) -> dict:
        """Full matrix: our own row plus every fresh remote row."""
        now = time.time()
        rows: dict[str, dict[str, int]] = {self._node_id: self._own_row()}
        for node, rec in self._remote.items():
            if now - rec["ts"] <= self._ttl:
                rows[node] = dict(rec["row"])
        nodes = sorted(set(rows) | {o for row in rows.values() for o in row})
        return {"nodes": nodes, "rows": rows, "rogue_node": None}
