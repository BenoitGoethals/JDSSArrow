"""JDSS-compliant client profiles (role personas).

Each profile is a *behaviour* bolted onto a generic simulated soldier system. It declares the
role, the message types it is expected to originate (its compliance surface), and three
hooks the scenario calls:

* ``on_start``  — announce identity / lay down initial graphics.
* ``on_tick``   — periodic behaviour (beacons, reports, orders…).
* ``on_message``— optional reaction to received traffic (e.g. a medic answering CASEVAC).

Profiles never build messages by hand beyond the typed JDSSDM models, so everything they emit
is valid by construction. They are discovered through the plugin registry
(``jdssarrow.profiles`` group), so new client types can be added without touching the engine.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from jdssarrow.datamodel.messages import (
    ChatRoom,
    Chatrooms,
    GeneralInfo,
    MessageType,
    Overlay,
    OverlayGraphic,
    Receipt,
    Sketch,
    SketchPoint,
)
from jdssarrow.datamodel.messages import Location as Loc
from jdssarrow.datamodel.symbology import StandardIdentity, sidc

if TYPE_CHECKING:
    from jdssarrow.datamodel.messages import JdssMessage
    from jdssarrow.simulator.scenario import SimClient


class ClientProfile(ABC):
    """Base class for a JDSS client persona."""

    #: role tag stamped into the node identity.
    role: str = "generic"
    #: physical device class this persona models (eud, atak, uav, vehicle, c2_workstation…).
    device: str = "generic"
    #: nation (alpha-3); overridable per client by the scenario.
    nation: str = "XXX"
    #: message types this role is expected to originate.
    emits: tuple[str, ...] = ()

    async def on_start(self, client: SimClient) -> None:
        """Announce identity by default."""
        await client.identify()

    @abstractmethod
    async def on_tick(self, client: SimClient, tick: int) -> None: ...

    async def on_message(self, client: SimClient, message: JdssMessage) -> None:
        """React to received traffic. Default: do nothing."""
        return None


class Rifleman(ClientProfile):
    role = "rifleman"
    device = "eud"
    emits = (MessageType.PRESENCE, MessageType.CHAT)

    async def on_tick(self, client: SimClient, tick: int) -> None:
        await client.presence(battery_pct=max(20, 100 - tick))
        if tick % 5 == 0 and tick:
            await client.chat("moving to next bound")


class TeamLeader(ClientProfile):
    role = "team_leader"
    device = "eud"
    emits = (
        MessageType.IDENTIFICATION,
        MessageType.PRESENCE,
        MessageType.CHAT,
        MessageType.CONTACT,
        MessageType.CASEVAC,
        MessageType.GENINFO,
    )

    async def on_tick(self, client: SimClient, tick: int) -> None:
        await client.presence()
        if tick == 2:
            # a soldier in the team is hit — request evacuation (drives the medic)
            await client.casevac(urgent=1)
        if tick % 4 == 0 and tick:
            await client.chat("SITREP: consolidating on objective")
        if tick % 6 == 0 and tick:
            await client.contact("dismounted patrol, 300m N", StandardIdentity.HOSTILE)
        if tick % 5 == 0 and tick:  # structured situation bulletin (GenInfo)
            lat, lon = client.pos
            await client.publish(
                GeneralInfo(
                    subject="SITREP",
                    text="objective consolidated, ammunition resupply requested",
                    location=Loc(lat=lat, lon=lon),
                ),
                MessageType.GENINFO,
            )


class Medic(ClientProfile):
    role = "medic"
    device = "eud"
    emits = (MessageType.PRESENCE, MessageType.CHAT, MessageType.RECEIPT)

    async def on_tick(self, client: SimClient, tick: int) -> None:
        await client.presence()

    async def on_message(self, client: SimClient, message: JdssMessage) -> None:
        # Compliance in action: answer any CASEVAC request and reposition to the casualty.
        if message.type == MessageType.CASEVAC:
            client.stats.casevac_acks += 1
            loc = getattr(message.body, "location", None)
            # formal Receipt keyed to the CASEVAC's message_id, plus a human-readable chat
            await client.publish(
                Receipt(ack_message_id=message.header.message_id, status="received"),
                MessageType.RECEIPT,
            )
            await client.chat(
                f"CASEVAC acknowledged, medic en route to {message.header.originator_id}",
                recipient=message.header.originator_id,
            )
            if loc is not None:
                await client.move_to(loc.lat, loc.lon)


class Scout(ClientProfile):
    role = "scout"
    device = "eud"
    emits = (MessageType.PRESENCE, MessageType.CONTACT, MessageType.SKETCH)

    async def on_tick(self, client: SimClient, tick: int) -> None:
        await client.presence()
        if tick % 2 == 0:
            await client.contact("vehicle movement", StandardIdentity.UNKNOWN)
        if tick % 5 == 0 and tick:
            lat, lon = client.pos
            await client.publish(
                Sketch(
                    title="route recce",
                    points=[
                        SketchPoint(location=Loc(lat=lat, lon=lon), label="start"),
                        SketchPoint(location=Loc(lat=lat + 0.002, lon=lon), label="ford"),
                    ],
                ),
                MessageType.SKETCH,
            )


class ForwardObserver(ClientProfile):
    role = "forward_observer"
    device = "tablet"
    emits = (MessageType.PRESENCE, MessageType.CONTACT, MessageType.OVERLAY)

    async def on_start(self, client: SimClient) -> None:
        await client.identify()
        lat, lon = client.pos
        await client.publish(
            Overlay(
                name="fire support coordination",
                graphics=[
                    OverlayGraphic(
                        sidc=sidc("control_point"),
                        location=Loc(lat=lat + 0.01, lon=lon + 0.01),
                        label="TRP-01",
                    )
                ],
            ),
            MessageType.OVERLAY,
        )

    async def on_tick(self, client: SimClient, tick: int) -> None:
        await client.presence()
        if tick % 3 == 0:
            await client.contact("target, danger close", StandardIdentity.HOSTILE)


class UavSensor(ClientProfile):
    role = "uav_sensor"
    device = "uav"
    emits = (MessageType.CONTACT, MessageType.PRESENCE)

    async def on_tick(self, client: SimClient, tick: int) -> None:
        # automated sensor: frequent detections + its own position beacon
        await client.presence(battery_pct=100)
        await client.contact("automated detection", StandardIdentity.UNKNOWN)


class CommandPost(ClientProfile):
    """HQ node: issues orders AND acts as the network monitor (common op picture)."""

    role = "command_post"
    device = "c2_workstation"
    emits = (MessageType.IDENTIFICATION, MessageType.CHAT)

    async def on_tick(self, client: SimClient, tick: int) -> None:
        if tick % 7 == 0 and tick:
            await client.chat("FRAGO: hold current positions")

    async def on_message(self, client: SimClient, message: JdssMessage) -> None:
        # The command post is the sink that builds the coalition COP from all traffic.
        client.stats.observe(message)


class AtakEud(ClientProfile):
    """An ATAK-style end-user device (Android Team Awareness Kit): position, chat, and map
    markers/overlays — the richest EUD in a coalition patrol."""

    role = "atak_operator"
    device = "atak"
    emits = (
        MessageType.IDENTIFICATION,
        MessageType.PRESENCE,
        MessageType.CHAT,
        MessageType.CONTACT,
        MessageType.OVERLAY,
        MessageType.CHATROOMS,
    )

    async def on_start(self, client: SimClient) -> None:
        await client.identify()
        lat, lon = client.pos
        await client.publish(
            Overlay(
                name="ATAK markers",
                graphics=[
                    OverlayGraphic(
                        sidc=sidc("control_point"),
                        location=Loc(lat=lat + 0.005, lon=lon - 0.003),
                        label="RALLY",
                    )
                ],
            ),
            MessageType.OVERLAY,
        )
        # ATAK devices enumerate their GeoChat rooms (Chatrooms)
        await client.publish(
            Chatrooms(
                rooms=[
                    ChatRoom(room_id="All Chat Rooms", name="All Chat Rooms"),
                    ChatRoom(room_id="Team", name="Team", members=[client.node_id]),
                ]
            ),
            MessageType.CHATROOMS,
        )

    async def on_tick(self, client: SimClient, tick: int) -> None:
        await client.presence(battery_pct=max(30, 100 - tick))
        if tick % 3 == 0:
            await client.contact("marker dropped on ATAK", StandardIdentity.UNKNOWN)
        if tick % 5 == 0 and tick:
            await client.chat("ATAK: checkpoint reached")


class Vehicle(ClientProfile):
    """A mounted / vehicle C2 platform — higher mobility, reports contacts on the move."""

    role = "mounted_c2"
    device = "vehicle"
    emits = (MessageType.IDENTIFICATION, MessageType.PRESENCE, MessageType.CONTACT)

    async def on_tick(self, client: SimClient, tick: int) -> None:
        # vehicles move faster — step twice per tick
        await client.presence()
        await client.presence()
        if tick % 4 == 0:
            await client.contact("convoy sighting", StandardIdentity.NEUTRAL)


#: local name → profile class map (also advertised as entry points for pluggability).
CLIENT_PROFILES: dict[str, type[ClientProfile]] = {
    "rifleman": Rifleman,
    "teamleader": TeamLeader,
    "medic": Medic,
    "scout": Scout,
    "observer": ForwardObserver,
    "sensor": UavSensor,
    "commandpost": CommandPost,
    "atak": AtakEud,
    "vehicle": Vehicle,
}
