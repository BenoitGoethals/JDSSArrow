"""Background simulation manager — start/stop a live simulation from the web UI.

Unlike :meth:`Simulation.run` (a fixed number of ticks), the manager drives a simulation in an
open-ended background task until it is stopped, so the operator can switch it on and watch the
dashboard come alive, then switch it off. Only one simulation runs at a time.

When pointed at the web node's own network/PSK/transport, the simulated coalition clients join
the *real* running network — so peers, feed, matrix and coalition policy all reflect them.
"""

from __future__ import annotations

import asyncio
import contextlib

from jdssarrow.simulator.scenario import Simulation


class SimulationManager:
    def __init__(self) -> None:
        self._sim: Simulation | None = None
        self._task: asyncio.Task | None = None
        self._ticks = 0
        self._interval = 1.0
        self._params: dict = {}

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(
        self,
        *,
        network_id: str,
        transport: str,
        codec: str,
        psk: str,
        security: str = "psk",
        interval: float = 1.0,
        rogue: str | None = None,
    ) -> dict:
        if self.running:
            raise RuntimeError("a simulation is already running")
        self._interval = max(0.05, interval)
        self._sim = Simulation(
            network_id=network_id,
            transport=transport,
            codec=codec,
            psk=psk,
            security=security,
            rogue=rogue,
        )
        self._params = {
            "network_id": network_id,
            "transport": transport,
            "codec": codec,
            "interval": self._interval,
            "rogue": rogue,
        }
        self._ticks = 0
        await self._sim.start()
        self._task = asyncio.create_task(self._loop())
        return self.status()

    async def _loop(self) -> None:
        assert self._sim is not None
        n = 0
        while True:
            await self._sim.tick(n)
            n += 1
            self._ticks = n
            await asyncio.sleep(self._interval)

    async def stop(self) -> dict:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._sim is not None:
            await self._sim.stop()
            self._sim = None
        return self.status()

    def status(self) -> dict:
        clients = []
        if self._sim is not None:
            clients = [
                {
                    "node_id": c.node_id,
                    "callsign": c.callsign,
                    "role": c.profile.role,
                    "device": c.profile.device,
                }
                for c in self._sim.clients
            ]
            if self._sim.rogue is not None:
                clients.append(
                    {
                        "node_id": self._sim.rogue.node_id,
                        "callsign": "RGUE-1",
                        "role": "rogue",
                        "device": "rogue",
                    }
                )
        return {
            "running": self.running,
            "ticks": self._ticks,
            "clients": clients,
            "client_count": len(clients),
            **self._params,
        }
