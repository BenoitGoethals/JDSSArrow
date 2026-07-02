"""Vol II — data model + codec round-trips."""

import pytest

from jdssarrow.datamodel import symbology
from jdssarrow.datamodel.codec.arrow_codec import ArrowCodec
from jdssarrow.datamodel.codec.json_codec import JsonCodec
from jdssarrow.datamodel.codec.xml_mip import XmlMipCodec
from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    JdssMessage,
    Location,
    MessageHeader,
    Presence,
)


def _presence() -> JdssMessage:
    return JdssMessage(
        header=MessageHeader(originator_id="node-a", sequence=7),
        body=Presence(location=Location(lat=50.85, lon=4.35), callsign="ALFA-1", battery_pct=88),
    )


def _casevac() -> JdssMessage:
    return JdssMessage(
        header=MessageHeader(originator_id="node-b"),
        body=CasevacRequest(location=Location(lat=50.9, lon=4.4), patients_urgent=2),
    )


def test_sidc_is_20_digits():
    code = symbology.sidc("medic", symbology.StandardIdentity.FRIEND)
    assert len(symbology.normalize(code)) == 20
    assert code.startswith("10")  # APP-6(D) version


@pytest.mark.parametrize("codec", [JsonCodec(), XmlMipCodec(), ArrowCodec()])
@pytest.mark.parametrize("factory", [_presence, _casevac])
def test_codec_roundtrip(codec, factory):
    original = factory()
    restored = codec.decode(codec.encode(original))
    assert restored.type == original.type
    assert restored.header.originator_id == original.header.originator_id
    assert restored.body == original.body


def test_arrow_batch_roundtrip():
    codec = ArrowCodec()
    msgs = [_presence(), _casevac(), _presence()]
    restored = codec.decode_batch(codec.encode_batch(msgs))
    assert [m.type for m in restored] == [m.type for m in msgs]


def test_chat_discriminated_union():
    msg = JdssMessage(
        header=MessageHeader(originator_id="x"), body=ChatMessage(text="hi", recipient="all")
    )
    raw = JsonCodec().encode(msg)
    assert JsonCodec().decode(raw).body.text == "hi"
