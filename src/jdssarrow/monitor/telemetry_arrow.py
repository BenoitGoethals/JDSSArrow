"""Apache Arrow telemetry ring buffer.

Recent messages are retained in a fixed-capacity, columnar Arrow buffer. Columnar layout
makes the monitor's queries ("last N", "count by type", "presence per node") cheap and lets
the ``/messages`` endpoint hand back an Arrow IPC stream for zero-copy analytics downstream.
This is the telemetry half of what makes JDSS *Arrow*.
"""

from __future__ import annotations

from collections import deque

from jdssarrow.datamodel.codec.arrow_codec import ArrowCodec
from jdssarrow.datamodel.messages import JdssMessage


class ArrowTelemetryBuffer:
    """Bounded FIFO of recent messages, queryable and Arrow-serializable."""

    def __init__(self, capacity: int = 1000) -> None:
        self._buf: deque[JdssMessage] = deque(maxlen=capacity)
        self._codec = ArrowCodec()

    def add(self, message: JdssMessage) -> None:
        self._buf.append(message)

    def recent(self, limit: int = 100) -> list[JdssMessage]:
        items = list(self._buf)
        return items[-limit:]

    def count_by_type(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for m in self._buf:
            counts[m.type] = counts.get(m.type, 0) + 1
        return counts

    def clear(self) -> int:
        """Drop all buffered telemetry; returns how many messages were cleared."""
        n = len(self._buf)
        self._buf.clear()
        return n

    def to_arrow_ipc(self, limit: int = 1000) -> bytes:
        """Serialize the recent window as an Arrow IPC stream."""
        return self._codec.encode_batch(self.recent(limit))

    def __len__(self) -> int:
        return len(self._buf)
