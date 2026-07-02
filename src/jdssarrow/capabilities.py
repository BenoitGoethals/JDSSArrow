"""Per-node capability matrix — which message types this node may receive / emit.

A small on/off permission matrix (rows = the 7 JDSS message types, columns = receive / emit),
editable at runtime from the Configuration tab. Enforcement:

* **receive** — an inbound message of a disallowed type is dropped on ingest (reason
  ``capability``), so it never reaches the operational picture.
* **emit** — the gateway refuses to originate a disallowed type (raises :class:`CapabilityError`).

Defaults to fully permissive, so the matrix is opt-in and changes nothing until you toggle it.
"""

from __future__ import annotations

from jdssarrow.datamodel.messages import MESSAGE_TYPES

MESSAGE_TYPE_NAMES: list[str] = [str(t) for t in MESSAGE_TYPES]


class CapabilityError(Exception):
    """Raised when a node tries to emit a message type its capability matrix forbids."""


class CapabilityMatrix:
    name = "capabilities"

    def __init__(
        self,
        receive: dict[str, bool] | None = None,
        emit: dict[str, bool] | None = None,
    ) -> None:
        self._recv: dict[str, bool] = {t: True for t in MESSAGE_TYPE_NAMES}
        self._emit: dict[str, bool] = {t: True for t in MESSAGE_TYPE_NAMES}
        if receive:
            self._recv.update({k: bool(v) for k, v in receive.items()})
        if emit:
            self._emit.update({k: bool(v) for k, v in emit.items()})

    def can_receive(self, message_type: str) -> bool:
        return self._recv.get(message_type, True)

    def can_emit(self, message_type: str) -> bool:
        return self._emit.get(message_type, True)

    def set(self, message_type: str, direction: str, allowed: bool) -> None:
        table = self._recv if direction == "receive" else self._emit
        if message_type in table:
            table[message_type] = bool(allowed)

    def snapshot(self) -> dict:
        return {
            "types": MESSAGE_TYPE_NAMES,
            "receive": dict(self._recv),
            "emit": dict(self._emit),
        }
