"""SoldierNode — a national soldier system driving a gateway.

A thin, ergonomic wrapper over :class:`JdssGateway` that models one dismounted soldier: it
knows how to announce presence, identify itself, report contacts, request CASEVAC and chat,
filling in sensible defaults from the node identity. This is the object the CLI and demos use.
"""

from __future__ import annotations

from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    ContactSighting,
    Identification,
    JdssMessage,
    Location,
    Presence,
)
from jdssarrow.datamodel.symbology import StandardIdentity
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.interfaces import MessageHandler


class SoldierNode:
    def __init__(self, gateway: JdssGateway) -> None:
        self.gateway = gateway
        self._id = gateway.config.identity

    async def start(self) -> None:
        await self.gateway.start()

    async def stop(self) -> None:
        await self.gateway.stop()

    def add_handler(self, handler: MessageHandler) -> None:
        self.gateway.add_handler(handler)

    # ----------------------------------------------------------- message helpers
    async def identify(self) -> JdssMessage:
        return await self.gateway.publish(
            Identification(
                callsign=self._id.callsign,
                unit=self._id.unit,
                role=self._id.role,
                nation=self._id.nation,
            )
        )

    async def presence(self, lat: float, lon: float, battery_pct: int | None = None) -> JdssMessage:
        return await self.gateway.publish(
            Presence(
                location=Location(lat=lat, lon=lon),
                callsign=self._id.callsign,
                battery_pct=battery_pct,
            )
        )

    async def report_contact(
        self,
        lat: float,
        lon: float,
        description: str = "",
        identity: StandardIdentity = StandardIdentity.HOSTILE,
    ) -> JdssMessage:
        return await self.gateway.publish(
            ContactSighting(
                location=Location(lat=lat, lon=lon),
                description=description,
                identity=identity,
            )
        )

    async def request_casevac(
        self, lat: float, lon: float, urgent: int = 1, priority: int = 0
    ) -> JdssMessage:
        return await self.gateway.publish(
            CasevacRequest(
                location=Location(lat=lat, lon=lon),
                patients_urgent=urgent,
                patients_priority=priority,
            )
        )

    async def chat(self, text: str, recipient: str = "all") -> JdssMessage:
        return await self.gateway.publish(ChatMessage(text=text, recipient=recipient))
