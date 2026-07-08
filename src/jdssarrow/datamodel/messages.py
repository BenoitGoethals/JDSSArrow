"""JDSSDM message models (Vol II).

A ``JdssMessage`` is a discriminated union over the ten AEP-76 JDSSDM message types. Each is a
pure pydantic model — no serialization or I/O logic lives here; that belongs to the codecs
(Single Responsibility). Every message carries a common :class:`MessageHeader` so the IEM
can route, deduplicate and authenticate uniformly regardless of body type.

The models mirror a MIP-3.1 / JC3IEDM subset: object items with a reporting data timestamp,
a geographic location, and an APP-6(D) SIDC.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from jdssarrow.datamodel import symbology


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex


class MessageType(StrEnum):
    PRESENCE = "Presence"
    IDENTIFICATION = "Identification"
    CONTACT = "ContactSighting"
    SKETCH = "Sketch"
    OVERLAY = "Overlay"
    CASEVAC = "CasevacRequest"
    CHAT = "Chat"
    GENINFO = "GenInfo"
    RECEIPT = "Receipt"
    CHATROOMS = "Chatrooms"


class Location(BaseModel):
    """WGS-84 point; altitude in metres HAE (optional)."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    alt_m: float | None = None


class MessageHeader(BaseModel):
    """Common envelope carried by every JDSS message."""

    message_id: str = Field(default_factory=_new_id)
    #: originating soldier/system id (also the JC3IEDM reporting-data source).
    originator_id: str
    #: coalition network the message belongs to.
    network_id: str = "default"
    reporting_time: datetime = Field(default_factory=_utcnow)
    #: monotonically increasing per originator; used by the IEM for dedup/ordering.
    sequence: int = 0
    #: security classification level 0..3 (see security.classification.Classification).
    classification: int = Field(default=0, ge=0, le=3)
    #: releasability caveat, e.g. "REL BEL NLD" or "ALL".
    releasable_to: str = "ALL"


class _Body(BaseModel):
    """Base for message bodies; ``type`` is the union discriminator."""


class Presence(_Body):
    """Position/status heartbeat of a friendly dismounted soldier.

    Carries the structured tactical attributes a receiver needs to render the correct APP-6(D) /
    2525D symbol: unit id + callsign (from the header/here), position, affiliation + unit type +
    status (encoded in ``sidc``), heading and speed, and the header's timestamp."""

    type: Literal[MessageType.PRESENCE] = MessageType.PRESENCE
    location: Location
    callsign: str
    battery_pct: int | None = Field(default=None, ge=0, le=100)
    #: course over ground in degrees true (0-360) and speed in m/s — "direction and speed".
    course_deg: float | None = Field(default=None, ge=0, le=360)
    speed_mps: float | None = Field(default=None, ge=0)
    sidc: str = Field(default_factory=lambda: symbology.sidc("dismounted_infantry"))

    @model_validator(mode="after")
    def _normalize_symbol(self) -> Presence:
        self.sidc = symbology.normalize(self.sidc)  # canonical 20-digit SIDC
        return self


class Identification(_Body):
    """Declares the identity/role of a node joining the network."""

    type: Literal[MessageType.IDENTIFICATION] = MessageType.IDENTIFICATION
    callsign: str
    unit: str
    role: str = "rifleman"
    nation: str = "XXX"  # ISO-3166 alpha-3 or "XXX" unknown
    sidc: str = Field(default_factory=lambda: symbology.sidc("dismounted_infantry"))


class ContactSighting(_Body):
    """Report of an observed contact (Contact/Sighting).

    The ``identity`` (affiliation) and ``status`` attributes drive the symbol: the SIDC is kept in
    sync with them so a receiver always renders the correct APP-6(D) / 2525D icon."""

    type: Literal[MessageType.CONTACT] = MessageType.CONTACT
    location: Location
    identity: symbology.StandardIdentity = symbology.StandardIdentity.HOSTILE
    status: symbology.Status = symbology.Status.PRESENT
    description: str = ""
    strength: int | None = None
    #: course over ground in degrees true (0-360) and speed in m/s — "direction and speed".
    course_deg: float | None = Field(default=None, ge=0, le=360)
    speed_mps: float | None = Field(default=None, ge=0)
    sidc: str = Field(
        default_factory=lambda: symbology.sidc(
            "hostile_contact", symbology.StandardIdentity.HOSTILE
        )
    )

    @model_validator(mode="after")
    def _sync_symbol(self) -> ContactSighting:
        # keep the SIDC's affiliation (digit 4) + status (digit 7) consistent with the attributes,
        # so the reported affiliation/status and the rendered symbol never disagree.
        code = symbology.with_identity(self.sidc, self.identity)
        self.sidc = symbology.normalize(code[:6] + str(int(self.status)) + code[7:])
        return self


class SketchPoint(BaseModel):
    location: Location
    label: str = ""


class Sketch(_Body):
    """Free-hand sketch: an ordered set of annotated points/segments."""

    type: Literal[MessageType.SKETCH] = MessageType.SKETCH
    title: str = "sketch"
    points: list[SketchPoint] = Field(default_factory=list)


class OverlayGraphic(BaseModel):
    sidc: str
    location: Location
    label: str = ""


class Overlay(_Body):
    """A tactical graphics overlay (control measures, graphics)."""

    type: Literal[MessageType.OVERLAY] = MessageType.OVERLAY
    name: str = "overlay"
    graphics: list[OverlayGraphic] = Field(default_factory=list)


class CasevacRequest(_Body):
    """9-line-style casualty evacuation request (subset)."""

    type: Literal[MessageType.CASEVAC] = MessageType.CASEVAC
    location: Location
    patients_urgent: int = 0
    patients_priority: int = 0
    patients_routine: int = 0
    special_equipment: str = "none"
    security_at_pickup: str = "no_enemy"
    sidc: str = Field(default_factory=lambda: symbology.sidc("casualty"))


class ChatMessage(_Body):
    """Free-text chat between coalition dismounted troops."""

    type: Literal[MessageType.CHAT] = MessageType.CHAT
    text: str
    recipient: str = "all"


class GeneralInfo(_Body):
    """GenInfo — free-form informational message (situation notes, general text broadcasts).

    Carries a short subject line plus free text, and an optional location so a note can be
    pinned to the map. Where Chat is conversational, GenInfo is a structured info bulletin."""

    type: Literal[MessageType.GENINFO] = MessageType.GENINFO
    subject: str = ""
    text: str
    location: Location | None = None

    @field_validator("subject", "text", mode="before")
    @classmethod
    def _coerce_str(cls, v: object) -> object:
        return "" if v is None else v  # an empty XML element decodes to None


class Receipt(_Body):
    """Receipt — acknowledgement that a specific message was received / read / rejected.

    ``ack_message_id`` references the acknowledged message's :attr:`MessageHeader.message_id`,
    so a sender can confirm delivery of e.g. a CASEVAC request or an order."""

    type: Literal[MessageType.RECEIPT] = MessageType.RECEIPT
    ack_message_id: str
    status: Literal["received", "read", "rejected"] = "received"
    note: str = ""

    @field_validator("note", mode="before")
    @classmethod
    def _coerce_note(cls, v: object) -> object:
        return "" if v is None else v


class ChatRoom(BaseModel):
    """One GeoChat room in a :class:`Chatrooms` enumeration."""

    room_id: str
    name: str = ""
    members: list[str] = Field(default_factory=list)

    @field_validator("name", mode="before")
    @classmethod
    def _coerce_name(cls, v: object) -> object:
        return "" if v is None else v

    @field_validator("members", mode="before")
    @classmethod
    def _coerce_members(cls, v: object) -> object:
        return v or []  # tolerate an empty XML element decoding to None


class Chatrooms(_Body):
    """Chatrooms — enumeration of available GeoChat rooms (and optionally their members)."""

    type: Literal[MessageType.CHATROOMS] = MessageType.CHATROOMS
    rooms: list[ChatRoom] = Field(default_factory=list)

    @field_validator("rooms", mode="before")
    @classmethod
    def _coerce_rooms(cls, v: object) -> object:
        return v or []  # an empty <rooms/> element decodes to None → treat as no rooms


Body = Annotated[
    Presence
    | Identification
    | ContactSighting
    | Sketch
    | Overlay
    | CasevacRequest
    | ChatMessage
    | GeneralInfo
    | Receipt
    | Chatrooms,
    Field(discriminator="type"),
]


class JdssMessage(BaseModel):
    """A complete JDSS message: header + typed body."""

    header: MessageHeader
    body: Body

    @property
    def type(self) -> str:
        return str(self.body.type)


MESSAGE_TYPES: dict[str, type[_Body]] = {
    MessageType.PRESENCE: Presence,
    MessageType.IDENTIFICATION: Identification,
    MessageType.CONTACT: ContactSighting,
    MessageType.SKETCH: Sketch,
    MessageType.OVERLAY: Overlay,
    MessageType.CASEVAC: CasevacRequest,
    MessageType.CHAT: ChatMessage,
    MessageType.GENINFO: GeneralInfo,
    MessageType.RECEIPT: Receipt,
    MessageType.CHATROOMS: Chatrooms,
}


def message_from_dict(data: dict) -> JdssMessage:
    """Rebuild a :class:`JdssMessage` from a plain dict (codec helper)."""
    return JdssMessage.model_validate(data)
