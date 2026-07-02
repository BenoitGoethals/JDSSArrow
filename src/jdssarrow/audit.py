"""Logging: an application log (errors/warnings) and a per-node message audit log.

Two distinct streams:

* **Application log** — standard Python ``logging`` for lifecycle/errors, captured into a
  bounded in-memory ring (``AppLogHandler``) so the dashboard can show recent WARN/ERROR.
* **Message audit log** (:class:`MessageLog`) — one entry per message the node handles, with
  ``direction`` (in/out), ``disposition`` (accepted/rejected) and, for rejections, the
  **reason why** (security, codec, framing, policy, capability, duplicate…).

The engine feeds the audit log via the metrics sink; the reason strings are the same ones used
for the drop counters, so the audit log and the Prometheus metrics always agree.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LogEntry:
    ts: float
    direction: str  # "in" | "out"
    disposition: str  # "accepted" | "rejected"
    reason: str | None = None
    type: str | None = None
    originator_id: str | None = None
    message_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "direction": self.direction,
            "disposition": self.disposition,
            "reason": self.reason,
            "type": self.type,
            "originator_id": self.originator_id,
            "message_id": self.message_id,
        }


class MessageLog:
    """Bounded audit trail of incoming/outgoing messages and why they were accepted/rejected."""

    def __init__(self, capacity: int = 1000) -> None:
        self._buf: deque[LogEntry] = deque(maxlen=capacity)

    def record(
        self,
        direction: str,
        disposition: str,
        *,
        reason: str | None = None,
        type: str | None = None,
        originator_id: str | None = None,
        message_id: str | None = None,
    ) -> None:
        self._buf.append(
            LogEntry(
                ts=time.time(),
                direction=direction,
                disposition=disposition,
                reason=reason,
                type=type,
                originator_id=originator_id,
                message_id=message_id,
            )
        )

    def recent(
        self,
        limit: int = 200,
        direction: str | None = None,
        disposition: str | None = None,
    ) -> list[dict[str, Any]]:
        items = list(self._buf)
        if direction:
            items = [e for e in items if e.direction == direction]
        if disposition:
            items = [e for e in items if e.disposition == disposition]
        return [e.as_dict() for e in items[-limit:]]

    def counts(self) -> dict[str, int]:
        c = {"in": 0, "out": 0, "accepted": 0, "rejected": 0}
        for e in self._buf:
            c[e.direction] = c.get(e.direction, 0) + 1
            c[e.disposition] = c.get(e.disposition, 0) + 1
        return c


# --------------------------------------------------------------- application log
@dataclass
class _AppRecords:
    buf: deque = field(default_factory=lambda: deque(maxlen=500))


_APP = _AppRecords()


class AppLogHandler(logging.Handler):
    """A logging handler that keeps recent records in a bounded ring for the dashboard."""

    def emit(self, record: logging.LogRecord) -> None:
        _APP.buf.append(
            {
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def app_log(limit: int = 200, min_level: str | None = None) -> list[dict[str, Any]]:
    items = list(_APP.buf)
    if min_level:
        threshold = logging.getLevelName(min_level)
        items = [r for r in items if logging.getLevelName(r["level"]) >= threshold]
    return items[-limit:]


_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Install the in-memory app-log handler (and a console handler) on the jdssarrow logger."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logger = logging.getLogger("jdssarrow")
    logger.setLevel(level)
    logger.addHandler(AppLogHandler())
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(console)
    logger.propagate = False
    _CONFIGURED = True
