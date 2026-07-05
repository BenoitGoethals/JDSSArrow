"""Monitoring endpoints — snapshot, Prometheus metrics, Arrow telemetry, message publish."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import BaseModel

from jdssarrow.datamodel import symbology
from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    ContactSighting,
    Identification,
    JdssMessage,
    Location,
    MessageHeader,
    Overlay,
    OverlayGraphic,
    Presence,
)
from jdssarrow.datamodel.symbology import StandardIdentity
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.web.deps import get_gateway

router = APIRouter(tags=["monitor"])


@router.get("/api/health")
async def health(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """System / monitor / thread health for the dashboard health panel.

    Declared async so it runs on the event loop and can count live asyncio tasks.
    """
    return gateway.health()


@router.get("/api/monitor/snapshot")
def snapshot(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    return gateway.metrics.snapshot()


@router.get("/api/monitor/peers")
def peers(gateway: JdssGateway = Depends(get_gateway)) -> list[dict]:
    """Who is connected: known peers with identity, freshness and classification."""
    return gateway.peers()


@router.get("/api/monitor/matrix")
def connection_matrix(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """Live N×N connection matrix, assembled from peer-digest gossip across the coalition net.

    Each node broadcasts the row it can see; this node collects them, so the matrix reflects
    the whole live network. A node that is being rejected (bad key) can't get its digest
    accepted, so it never appears as a column.
    """
    return gateway.connection_matrix()


@router.get("/api/monitor/matrix/probe")
async def connection_matrix_probe(ticks: int = 12, rogue: str | None = None) -> dict:
    """Connectivity matrix from an in-process reachability *probe* (the simulator).

    Useful to demonstrate rejection deterministically — pass ``rogue=garbage`` and watch the
    rogue's column stay all-zero — without needing a rogue on the real network.
    """
    import secrets

    from jdssarrow.simulator.scenario import Simulation

    sim = Simulation(
        network_id=f"matrix-probe-{secrets.token_hex(4)}",  # isolate concurrent probes
        transport="loopback",
        codec="xml",
        rogue=rogue,
    )
    report = await sim.run(ticks=max(1, min(ticks, 40)), tick_interval=0.0)
    return {"nodes": report.nodes, "rows": report.matrix, "rogue_node": report.rogue_node}


@router.get("/api/messages")
def recent_messages(limit: int = 100, gateway: JdssGateway = Depends(get_gateway)) -> list[dict]:
    """Recent messages from the Arrow telemetry buffer, as JSON."""
    msgs = gateway.metrics.telemetry.recent(limit)
    return [m.model_dump(mode="json") for m in msgs]


@router.get("/api/messages.arrow")
def recent_messages_arrow(
    limit: int = 1000, gateway: JdssGateway = Depends(get_gateway)
) -> Response:
    """Recent telemetry as a raw Apache Arrow IPC stream (zero-copy analytics)."""
    data = gateway.metrics.telemetry.to_arrow_ipc(limit)
    return Response(content=data, media_type="application/vnd.apache.arrow.stream")


@router.get("/metrics")
def prometheus(gateway: JdssGateway = Depends(get_gateway)) -> Response:
    return Response(content=gateway.metrics.prometheus(), media_type=CONTENT_TYPE_LATEST)


# --------------------------------------------------------------- publish helpers
class PresenceIn(BaseModel):
    lat: float
    lon: float
    callsign: str = "WEB-1"
    battery_pct: int | None = None
    #: APP-6D standard identity (0-6). Presence is friendly by default.
    identity: int | None = None
    #: optional caller-supplied SIDC; used only if it is a valid 20-digit 2525D code.
    sidc: str | None = None


class ChatIn(BaseModel):
    text: str
    recipient: str = "all"


class ContactIn(BaseModel):
    lat: float
    lon: float
    description: str = ""
    #: APP-6D standard identity (0-6). Defaults to HOSTILE for a contact sighting.
    identity: int | None = None
    #: optional caller-supplied SIDC; used only if it is a valid 20-digit 2525D code.
    sidc: str | None = None
    strength: int | None = None


def _identity(value: int | None, default: StandardIdentity) -> StandardIdentity:
    """Coerce an incoming identity digit to a StandardIdentity, else the default."""
    try:
        return StandardIdentity(int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _compliant_sidc(raw: str | None, identity: StandardIdentity, entity: str) -> str:
    """Return a valid 20-digit APP-6D SIDC whose affiliation digit matches ``identity``.

    If the caller passed a conforming SIDC we keep its symbol set / entity and only
    re-stamp the affiliation; otherwise we synthesise one from a known entity. Either
    way the receiving client renders the correct symbol from the SIDC attributes.
    """
    if raw and symbology.is_valid_sidc(raw):
        return symbology.with_identity(raw, identity)
    return symbology.sidc(entity, identity)


@router.post("/api/publish/presence")
async def publish_presence(body: PresenceIn, gateway: JdssGateway = Depends(get_gateway)) -> dict:
    identity = _identity(body.identity, StandardIdentity.FRIEND)
    msg = await gateway.publish(
        Presence(
            location=Location(lat=body.lat, lon=body.lon),
            callsign=body.callsign,
            battery_pct=body.battery_pct,
            sidc=_compliant_sidc(body.sidc, identity, "dismounted_infantry"),
        )
    )
    return {"message_id": msg.header.message_id}


@router.post("/api/publish/chat")
async def publish_chat(body: ChatIn, gateway: JdssGateway = Depends(get_gateway)) -> dict:
    msg = await gateway.publish(ChatMessage(text=body.text, recipient=body.recipient))
    return {"message_id": msg.header.message_id}


@router.post("/api/publish/contact")
async def publish_contact(body: ContactIn, gateway: JdssGateway = Depends(get_gateway)) -> dict:
    identity = _identity(body.identity, StandardIdentity.HOSTILE)
    msg = await gateway.publish(
        ContactSighting(
            location=Location(lat=body.lat, lon=body.lon),
            identity=identity,
            description=body.description,
            strength=body.strength,
            sidc=_compliant_sidc(body.sidc, identity, "hostile_contact"),
        )
    )
    return {"message_id": msg.header.message_id}


class InjectIn(BaseModel):
    """A message injected into the gateway *under its own originator* (e.g. an external simulator).

    Unlike /api/publish/* (which sends under this node's identity), an injected message is folded
    in as if received from that originator, so the gateway fans it out to every connected client —
    ATAK EUDs, TAK servers, the dashboard and coalition multicast peers."""

    originator: str
    type: str  # Presence | Identification | ContactSighting | CasevacRequest | Chat | Overlay
    callsign: str | None = None
    lat: float | None = None
    lon: float | None = None
    course_deg: float | None = None
    speed_mps: float | None = None
    battery_pct: int | None = None
    identity: int | None = None
    sidc: str | None = None
    description: str | None = None
    strength: int | None = None
    text: str | None = None
    recipient: str = "all"
    unit: str | None = None
    role: str | None = None
    nation: str | None = None
    classification: int | None = None
    releasable_to: str | None = None


def _inject_body(b: InjectIn) -> object:
    loc = Location(lat=b.lat or 0.0, lon=b.lon or 0.0)
    if b.type == "Presence":
        ident = _identity(b.identity, StandardIdentity.FRIEND)
        return Presence(
            location=loc, callsign=b.callsign or b.originator, battery_pct=b.battery_pct,
            course_deg=b.course_deg, speed_mps=b.speed_mps,
            sidc=_compliant_sidc(b.sidc, ident, "dismounted_infantry"),
        )
    if b.type == "ContactSighting":
        ident = _identity(b.identity, StandardIdentity.HOSTILE)
        return ContactSighting(
            location=loc, identity=ident, description=b.description or "contact",
            strength=b.strength, course_deg=b.course_deg, speed_mps=b.speed_mps,
            sidc=_compliant_sidc(b.sidc, ident, "hostile_contact"),
        )
    if b.type == "Identification":
        return Identification(
            callsign=b.callsign or b.originator, unit=b.unit or b.callsign or b.originator,
            role=b.role or "rifleman", nation=b.nation or "XXX",
        )
    if b.type == "CasevacRequest":
        return CasevacRequest(location=loc, patients_urgent=b.strength or 1)
    if b.type == "Chat":
        return ChatMessage(text=b.text or "—", recipient=b.recipient)
    if b.type == "Overlay":
        return Overlay(
            name=b.description or "overlay",
            graphics=[OverlayGraphic(
                sidc=symbology.sidc("control_point"), location=loc, label=b.callsign or "OBJ"
            )],
        )
    raise HTTPException(status_code=400, detail=f"unknown message type '{b.type}'")


@router.post("/api/inject")
async def inject(body: InjectIn, gateway: JdssGateway = Depends(get_gateway)) -> dict:
    """Inject a message into the gateway under ``originator`` and fan it out to every client."""
    cfg = gateway.config
    header = MessageHeader(
        originator_id=body.originator,
        network_id=cfg.network.network_id,
        classification=body.classification
        if body.classification is not None
        else cfg.classification.level,
        releasable_to=body.releasable_to or cfg.classification.releasable_to,
    )
    message = JdssMessage(header=header, body=_inject_body(body))  # type: ignore[arg-type]
    await gateway.ingest_from_bridge(message)
    return {"ok": True, "type": body.type, "originator": body.originator,
            "message_id": message.header.message_id}
