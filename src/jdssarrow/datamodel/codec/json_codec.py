"""JSON codec — the simplest reference serialization of the JDSSDM.

Useful for debugging, the web API and tests. Round-trips a :class:`JdssMessage` through
pydantic's JSON support with no information loss.
"""

from __future__ import annotations

from jdssarrow.datamodel.messages import JdssMessage


class JsonCodec:
    name = "json"
    content_type = "application/json"

    def encode(self, message: JdssMessage) -> bytes:
        return message.model_dump_json().encode("utf-8")

    def decode(self, raw: bytes) -> JdssMessage:
        return JdssMessage.model_validate_json(raw.decode("utf-8"))
