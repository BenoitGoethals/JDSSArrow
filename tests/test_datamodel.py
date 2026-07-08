"""Vol II — data model + codec round-trips."""

import pytest

from jdssarrow.datamodel import symbology
from jdssarrow.datamodel.codec.arrow_codec import ArrowCodec
from jdssarrow.datamodel.codec.json_codec import JsonCodec
from jdssarrow.datamodel.codec.xml_mip import XmlMipCodec
from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ChatMessage,
    ChatRoom,
    Chatrooms,
    GeneralInfo,
    JdssMessage,
    Location,
    MessageHeader,
    Presence,
    Receipt,
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


def _geninfo() -> JdssMessage:
    return JdssMessage(
        header=MessageHeader(originator_id="cp"),
        body=GeneralInfo(subject="SITREP", text="objective secured",
                         location=Location(lat=50.8, lon=4.3)),
    )


def _receipt() -> JdssMessage:
    return JdssMessage(
        header=MessageHeader(originator_id="med"),
        body=Receipt(ack_message_id="deadbeef", status="rejected", note="over classification"),
    )


def _chatrooms() -> JdssMessage:
    return JdssMessage(
        header=MessageHeader(originator_id="atak"),
        body=Chatrooms(rooms=[
            ChatRoom(room_id="All Chat Rooms", name="All Chat Rooms"),
            ChatRoom(room_id="alpha", name="Alpha", members=["a", "b"]),
        ]),
    )


def _chatrooms_empty() -> JdssMessage:
    return JdssMessage(header=MessageHeader(originator_id="atak"), body=Chatrooms())


def test_sidc_is_20_digits():
    code = symbology.sidc("medic", symbology.StandardIdentity.FRIEND)
    assert len(symbology.normalize(code)) == 20
    assert code.startswith("10")  # APP-6(D) version


@pytest.mark.parametrize("codec", [JsonCodec(), XmlMipCodec(), ArrowCodec()])
@pytest.mark.parametrize(
    "factory",
    [_presence, _casevac, _geninfo, _receipt, _chatrooms, _chatrooms_empty],
)
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


def test_full_jdssdm_message_set():
    from jdssarrow.datamodel.messages import MESSAGE_TYPES, MessageType, message_from_dict

    # the ten JDSSDM message types, including GenInfo / Receipt / Chatrooms
    assert set(MESSAGE_TYPES) == {str(t) for t in MessageType}
    assert len(MESSAGE_TYPES) == 10
    assert {"GenInfo", "Receipt", "Chatrooms"} <= set(MESSAGE_TYPES)

    # each rebuilds from a plain dict under the right body class (discriminator wiring)
    for factory in (_geninfo, _receipt, _chatrooms):
        original = factory()
        rebuilt = message_from_dict(original.model_dump(mode="json"))
        assert rebuilt.body == original.body


def test_receipt_references_acked_message_id():
    ack = JdssMessage(header=MessageHeader(originator_id="cp"),
                      body=CasevacRequest(location=Location(lat=1, lon=2)))
    receipt = Receipt(ack_message_id=ack.header.message_id, status="received")
    assert receipt.ack_message_id == ack.header.message_id
