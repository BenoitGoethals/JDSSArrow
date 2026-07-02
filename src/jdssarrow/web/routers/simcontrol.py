"""Start/stop a live simulation from the dashboard.

By default the simulation is launched onto the web node's *own* network (same network id, PSK,
transport and codec), so the simulated coalition clients become real peers of this node and the
whole dashboard — peers, feed, matrix, coalition policy — reflects them.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from jdssarrow.web.deps import get_gateway

router = APIRouter(tags=["simulation"])


class SimStart(BaseModel):
    interval: float = 1.0
    rogue: str | None = None  # None | "wrong_key" | "garbage" | "insider"
    transport: str | None = None  # default: match this node's transport
    isolated: bool = False  # True → run on a separate loopback network


@router.get("/api/sim")
def sim_status(request: Request) -> dict:
    return request.app.state.sim_manager.status()


@router.post("/api/sim/start")
async def sim_start(body: SimStart, request: Request) -> dict:
    gateway = get_gateway(request)
    manager = request.app.state.sim_manager
    if body.isolated:
        network_id, transport, psk = "sim-isolated", "loopback", "sim-key"
    else:
        # join this node's real network so its dashboard lights up
        network_id = gateway.config.network.network_id
        transport = body.transport or gateway.config.plugins.transport
        psk = gateway.config.network.psk
    try:
        return await manager.start(
            network_id=network_id,
            transport=transport,
            codec=gateway.config.plugins.codec,
            psk=psk,
            interval=body.interval,
            rogue=body.rogue,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/sim/stop")
async def sim_stop(request: Request) -> dict:
    return await request.app.state.sim_manager.stop()
