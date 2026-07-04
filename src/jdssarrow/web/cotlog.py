"""A bounded, inspectable log of CoT frames crossing the TAK-server / EUD bridges.

Each entry records one CoT frame sent to a server/EUD (``out``: JDSS -> CoT) or received from one
(``in``: CoT -> JDSS), with the parsed type/uid/callsign and the raw XML, so the operator can see
exactly what traffic is flowing to the TAK servers from the dashboard.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any
from xml.etree import ElementTree as ET

_MAX_RAW = 8192  # cap stored raw XML so a huge frame can't bloat the ring


def _parse(raw: bytes) -> dict[str, Any]:
    """Best-effort extraction of the fields worth showing in the traffic list."""
    text = raw.decode("utf-8", "replace")
    cot_type = uid = callsign = remarks = None
    try:
        root = ET.fromstring(raw)
        if root.tag == "event":
            cot_type = root.get("type")
            uid = root.get("uid")
            detail = root.find("detail")
            if detail is not None:
                contact = detail.find("contact")
                if contact is not None:
                    callsign = contact.get("callsign")
                rem = detail.find("remarks")
                if rem is not None and rem.text:
                    remarks = rem.text
    except ET.ParseError:
        pass
    return {
        "type": cot_type,
        "uid": uid,
        "callsign": callsign,
        "remarks": remarks,
        "size": len(raw),
        "raw": text[:_MAX_RAW],
    }


class CotTrafficLog:
    def __init__(self, capacity: int = 500) -> None:
        self._buf: deque[dict[str, Any]] = deque(maxlen=capacity)

    def record(self, *, peer: str, direction: str, raw: bytes) -> None:
        """direction: 'out' = JDSS→CoT sent to the peer, 'in' = CoT→JDSS received from it."""
        self._buf.append({"ts": time.time(), "peer": peer, "direction": direction, **_parse(raw)})

    def recent(self, limit: int = 200, peer: str | None = None) -> list[dict[str, Any]]:
        items = [e for e in self._buf if peer is None or e["peer"] == peer]
        return items[-limit:]

    def counts(self) -> dict[str, int]:
        c = {"in": 0, "out": 0}
        for e in self._buf:
            c[e["direction"]] = c.get(e["direction"], 0) + 1
        return c

    def clear(self) -> int:
        n = len(self._buf)
        self._buf.clear()
        return n
