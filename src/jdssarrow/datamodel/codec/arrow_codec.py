"""Apache Arrow codec — columnar serialization for bulk transfer and telemetry.

While XML/JSON serialize one message at a time, Arrow shines when many homogeneous records
move together (e.g. flushing a batch of Presence heartbeats, or the monitor's telemetry
ring buffer). This codec satisfies the single-message ``Codec`` protocol by writing a
one-row Arrow IPC stream, and additionally exposes :meth:`encode_batch` / :meth:`decode_batch`
that the monitor uses for efficient columnar history. This is the component that gives the
project its name.
"""

from __future__ import annotations

import io

import pyarrow as pa

from jdssarrow.datamodel.messages import JdssMessage, message_from_dict

_SCHEMA = pa.schema(
    [
        ("message_id", pa.string()),
        ("originator_id", pa.string()),
        ("network_id", pa.string()),
        ("reporting_time", pa.string()),
        ("sequence", pa.int64()),
        ("classification", pa.int64()),
        ("releasable_to", pa.string()),
        ("type", pa.string()),
        ("body_json", pa.string()),  # full body preserved losslessly as JSON
    ]
)


def _to_row(message: JdssMessage) -> dict:
    h = message.header
    return {
        "message_id": h.message_id,
        "originator_id": h.originator_id,
        "network_id": h.network_id,
        "reporting_time": h.reporting_time.isoformat(),
        "sequence": h.sequence,
        "classification": h.classification,
        "releasable_to": h.releasable_to,
        "type": message.type,
        "body_json": message.body.model_dump_json(),
    }


def _from_row(row: dict) -> JdssMessage:
    import json

    return message_from_dict(
        {
            "header": {
                "message_id": row["message_id"],
                "originator_id": row["originator_id"],
                "network_id": row["network_id"],
                "reporting_time": row["reporting_time"],
                "sequence": row["sequence"],
                "classification": row["classification"],
                "releasable_to": row["releasable_to"],
            },
            "body": json.loads(row["body_json"]),
        }
    )


class ArrowCodec:
    name = "arrow"
    content_type = "application/vnd.apache.arrow.stream"

    def encode(self, message: JdssMessage) -> bytes:
        return self.encode_batch([message])

    def decode(self, raw: bytes) -> JdssMessage:
        messages = self.decode_batch(raw)
        if not messages:
            raise ValueError("empty Arrow stream")
        return messages[0]

    # ----------------------------------------------------------------- batch API
    def encode_batch(self, messages: list[JdssMessage]) -> bytes:
        table = pa.Table.from_pylist([_to_row(m) for m in messages], schema=_SCHEMA)
        sink = io.BytesIO()
        with pa.ipc.new_stream(sink, _SCHEMA) as writer:
            writer.write_table(table)
        return sink.getvalue()

    def decode_batch(self, raw: bytes) -> list[JdssMessage]:
        with pa.ipc.open_stream(io.BytesIO(raw)) as reader:
            table = reader.read_all()
        return [_from_row(row) for row in table.to_pylist()]
