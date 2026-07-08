"""Cursor-on-Target (CoT) ↔ JDSSDM translation.

ATAK speaks CoT XML events; JDSSArrow speaks the JDSSDM message set. This module is the pure,
I/O-free mapping between the two, so it can be unit-tested in isolation. The
:mod:`jdssarrow.bridges.atak` bridge wires it to UDP multicast on both sides.

Mapping (both directions):

    CoT ``a-f-*``            ↔  Presence            (friendly track)
    CoT ``a-h/-u/-n-*``      ↔  ContactSighting     (affiliation → StandardIdentity)
    CoT GeoChat ``b-t-f``    ↔  Chat
    CoT ``u-d-*``/``b-m-p-*``↔  Overlay             (map marker / drawing)
    CoT medevac ``b-r-f-h-c``↔  CasevacRequest      (9-line)

Emitted CoT carries a ``<__jdssbridge/>`` marker so the bridge can ignore its own echoes on a
multicast group with loopback enabled.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    ContactSighting,
    GeneralInfo,
    JdssMessage,
    Location,
    MessageHeader,
    Overlay,
    OverlayGraphic,
    Presence,
)
from jdssarrow.datamodel.symbology import StandardIdentity, sidc

BRIDGE_MARKER = "__jdssbridge"

# CoT affiliation letter (2nd token of an "atoms" type) → JDSS standard identity
_AFF_TO_IDENTITY = {
    "f": StandardIdentity.FRIEND,
    "h": StandardIdentity.HOSTILE,
    "n": StandardIdentity.NEUTRAL,
    "u": StandardIdentity.UNKNOWN,
    "p": StandardIdentity.PENDING,
}
_IDENTITY_TO_AFF = {v: k for k, v in _AFF_TO_IDENTITY.items()}


def _point(root: ET.Element) -> tuple[float, float]:
    p = root.find("point")
    if p is None:
        return 0.0, 0.0
    return float(p.get("lat", "0") or 0), float(p.get("lon", "0") or 0)


def cot_to_message(raw: bytes, originator: str) -> JdssMessage | None:
    """Translate a CoT event into a JDSSDM message, or None if it isn't mappable."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    if root.tag != "event":
        return None

    cot_type = root.get("type", "")
    lat, lon = _point(root)
    detail = root.find("detail")
    callsign, remarks = "", ""
    course = speed = None
    if detail is not None:
        contact = detail.find("contact")
        if contact is not None:
            callsign = contact.get("callsign", "")
        rem = detail.find("remarks")
        if rem is not None and rem.text:
            remarks = rem.text
        track = detail.find("track")  # ATAK's course (deg true) + speed (m/s)
        if track is not None:
            course = _deg(track.get("course"))
            speed = _spd(track.get("speed"))

    header = MessageHeader(originator_id=originator)

    if cot_type.startswith("b-t-f"):  # GeoChat
        text = remarks or callsign or ""
        return JdssMessage(header=header, body=ChatMessage(text=text, recipient="all"))
    if cot_type.startswith("b-r-f-h-c"):  # medevac 9-line
        return JdssMessage(
            header=header,
            body=CasevacRequest(location=Location(lat=lat, lon=lon), patients_urgent=1),
        )
    if cot_type.startswith("u-d") or cot_type.startswith("b-m-p"):  # drawing / marker
        return JdssMessage(
            header=header,
            body=Overlay(
                name=callsign or "ATAK marker",
                graphics=[
                    OverlayGraphic(
                        sidc=sidc("control_point"),
                        location=Location(lat=lat, lon=lon),
                        label=remarks or callsign or "marker",
                    )
                ],
            ),
        )
    if cot_type.startswith("a-"):
        parts = cot_type.split("-")
        aff = parts[1] if len(parts) > 1 else "u"
        if aff == "f":
            return JdssMessage(
                header=header,
                body=Presence(
                    location=Location(lat=lat, lon=lon),
                    callsign=callsign or "ATAK",
                    course_deg=course,
                    speed_mps=speed,
                ),
            )
        return JdssMessage(
            header=header,
            body=ContactSighting(
                location=Location(lat=lat, lon=lon),
                description=remarks or callsign or "CoT track",
                identity=_AFF_TO_IDENTITY.get(aff, StandardIdentity.UNKNOWN),
                course_deg=course,
                speed_mps=speed,
            ),
        )
    return None


def _num(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        n = float(value)
    except ValueError:
        return None
    return None if (n != n or n in (float("inf"), float("-inf"))) else n  # reject NaN/inf


def _deg(value: str | None) -> float | None:
    """Course over ground, or None for ATAK's unknown sentinels (9999999, NaN)."""
    n = _num(value)
    return n if n is not None and 0 <= n <= 360 else None


def _spd(value: str | None) -> float | None:
    n = _num(value)
    return n if n is not None and 0 <= n < 1_000_000 else None


def message_to_cot(message: JdssMessage, stale_s: int = 300) -> bytes | None:
    """Translate a JDSSDM message into a CoT event, or None if it has no CoT representation.

    ``stale_s`` sets the CoT ``stale`` window. Presence gets a **stable per-node uid**
    (``JDSS.<originator>``) so ATAK updates one moving track and auto-expires it when beacons
    stop; other events get a per-message uid.
    """
    body = message.body

    cot_type: str
    lat = lon = 0.0
    callsign = ""
    remarks = ""

    # isinstance dispatch narrows the discriminated union for the type checker
    if isinstance(body, Presence):
        cot_type = "a-f-G-U-C"
        lat, lon = body.location.lat, body.location.lon
        callsign = body.callsign
    elif isinstance(body, ContactSighting):
        aff = _IDENTITY_TO_AFF.get(body.identity, "u")
        cot_type = f"a-{aff}-G"
        lat, lon = body.location.lat, body.location.lon
        remarks = body.description
    elif isinstance(body, CasevacRequest):
        cot_type = "b-r-f-h-c"
        lat, lon = body.location.lat, body.location.lon
        remarks = f"CASEVAC urgent={body.patients_urgent}"
    elif isinstance(body, Overlay):
        if not body.graphics:
            return None
        g = body.graphics[0]
        cot_type = "u-d-p"
        lat, lon = g.location.lat, g.location.lon
        callsign = body.name
        remarks = g.label
    elif isinstance(body, ChatMessage):
        cot_type = "b-t-f"
        remarks = body.text
    elif isinstance(body, GeneralInfo):  # free-form info → GeoChat text (optionally located)
        cot_type = "b-t-f"
        remarks = f"{body.subject}: {body.text}" if body.subject else body.text
        if body.location is not None:
            lat, lon = body.location.lat, body.location.lon
    else:  # Identification, Sketch, Receipt (ack — no native CoT), Chatrooms → no CoT event
        return None

    t = message.header.reporting_time
    stale = t + timedelta(seconds=stale_s)
    # a node's Presence is one persistent track (stable uid); everything else is a discrete event
    if isinstance(body, Presence):
        uid = f"JDSS.{message.header.originator_id}"
    else:
        uid = f"JDSS.{message.header.originator_id}.{message.header.message_id[:8]}"

    ev = ET.Element(
        "event",
        {
            "version": "2.0",
            "uid": uid,
            "type": cot_type,
            "how": "m-g",
            "time": _cot_time(t),
            "start": _cot_time(t),
            "stale": _cot_time(stale),
        },
    )
    ET.SubElement(
        ev,
        "point",
        {"lat": str(lat), "lon": str(lon), "hae": "0", "ce": "9999999", "le": "9999999"},
    )
    detail = ET.SubElement(ev, "detail")
    ET.SubElement(detail, BRIDGE_MARKER)  # so the bridge ignores its own echoes
    if callsign:
        ET.SubElement(detail, "contact", {"callsign": callsign})
    if remarks:
        ET.SubElement(detail, "remarks").text = remarks
    # movement — ATAK renders a leader/velocity vector from course (deg true) + speed (m/s)
    course = getattr(body, "course_deg", None)
    speed = getattr(body, "speed_mps", None)
    if course is not None or speed is not None:
        ET.SubElement(
            detail,
            "track",
            {"course": str(course if course is not None else 0.0),
             "speed": str(speed if speed is not None else 0.0)},
        )
    return ET.tostring(ev, encoding="utf-8")


def cot_is_stale(raw: bytes, now: datetime | None = None) -> bool:
    """True if a CoT event's ``stale`` time is already in the past (a dead track)."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return False
    stale = root.get("stale")
    if not stale:
        return False
    try:
        dt = datetime.fromisoformat(stale.replace("Z", "+00:00"))
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt < (now or datetime.now(UTC))


def _cot_time(dt: datetime) -> str:
    """CoT/TAK Zulu timestamp, e.g. ``2026-07-05T12:38:32.348Z``.

    ATAK/WinTAK require this exact shape (UTC, milliseconds, trailing ``Z``); a Python ``+00:00``
    offset or 6-digit microseconds makes ATAK silently drop the event instead of rendering it."""
    return dt.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def cot_delete(uid: str, now: datetime | None = None) -> bytes:
    """Build a CoT delete event (``t-x-d-d``) instructing clients to drop track ``uid``."""
    now = now or datetime.now(UTC)
    iso = _cot_time(now)
    ev = ET.Element(
        "event",
        {"version": "2.0", "uid": f"{uid}.del", "type": "t-x-d-d", "how": "m-g",
         "time": iso, "start": iso, "stale": iso},
    )
    ET.SubElement(
        ev, "point", {"lat": "0", "lon": "0", "hae": "0", "ce": "9999999", "le": "9999999"}
    )
    detail = ET.SubElement(ev, "detail")
    ET.SubElement(detail, BRIDGE_MARKER)
    ET.SubElement(detail, "link", {"uid": uid, "relation": "p-p", "type": "none"})
    return ET.tostring(ev, encoding="utf-8")
