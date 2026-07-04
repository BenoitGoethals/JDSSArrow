"""Configure the built-in ATAK/EUD TAK server from the Configuration tab.

Toggling it on makes the node listen for ATAK connections; changes apply live and persist to the
``eud_server`` section of the config file.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from jdssarrow.config.models import EudServerConfig
from jdssarrow.web.deps import get_gateway
from jdssarrow.web.runtime import persist_config_section

router = APIRouter(tags=["eud"])


class EudServerInput(BaseModel):
    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8087, ge=1, le=65535)
    advertised_host: str | None = None


@router.get("/api/eud")
def get_eud(request: Request) -> dict:
    """Current EUD server config + live status (listening, connected clients, LAN IP)."""
    return request.app.state.eud_server.status()


@router.get("/api/eud/log")
def eud_traffic(request: Request, limit: int = 200) -> dict:
    """Recent CoT frames to/from connected ATAK EUDs (out = JDSS→CoT, in = CoT→JDSS)."""
    mgr = request.app.state.eud_server
    return {"counts": mgr.traffic.counts(), "entries": mgr.traffic.recent(limit)}


@router.put("/api/eud")
async def update_eud(body: EudServerInput, request: Request) -> dict:
    """Apply the EUD server settings live and persist them."""
    cfg = EudServerConfig(**body.model_dump())
    get_gateway(request).config.eud_server = cfg
    await request.app.state.eud_server.reconfigure(cfg)
    persist_config_section(request.app, "eud_server", cfg.model_dump())
    return request.app.state.eud_server.status()
