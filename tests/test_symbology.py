"""APP-6(D) / MIL-STD-2525D conformance: structured attributes + SIDC validation."""

from __future__ import annotations

import pytest

from jdssarrow.datamodel import symbology as s
from jdssarrow.datamodel.messages import (
    CasevacRequest,
    ContactSighting,
    Identification,
    JdssMessage,
    Location,
    MessageHeader,
    Overlay,
    OverlayGraphic,
    Presence,
)


def _bodies():
    return [
        Presence(location=Location(lat=1, lon=2), callsign="A"),
        Identification(callsign="A", unit="U"),
        ContactSighting(location=Location(lat=1, lon=2), description="x"),
        CasevacRequest(location=Location(lat=1, lon=2)),
        Overlay(
            graphics=[OverlayGraphic(sidc=s.sidc("control_point"), location=Location(lat=1, lon=2))]
        ),
    ]


def test_every_message_sidc_is_valid_2525d():
    for body in _bodies():
        codes = [body.sidc] if hasattr(body, "sidc") else [g.sidc for g in body.graphics]
        for code in codes:
            assert len(code) == 20 and code.isdigit()
            assert s.validate_sidc(code) == [], f"{type(body).__name__}: {code}"


def test_affiliation_attribute_drives_the_symbol():
    for ident in s.StandardIdentity:
        c = ContactSighting(location=Location(lat=1, lon=2), identity=ident)
        assert s.parse_sidc(c.sidc)["identity"] == str(int(ident))  # digit 4 == affiliation
        assert s.is_valid_sidc(c.sidc)


def test_status_attribute_reflected_in_symbol():
    for status in s.Status:
        c = ContactSighting(location=Location(lat=1, lon=2), status=status)
        assert s.parse_sidc(c.sidc)["status"] == str(int(status))  # digit 7 == status
        assert s.is_valid_sidc(c.sidc)


def test_control_measure_entity_uses_its_own_symbol_set():
    code = s.sidc("control_point")
    assert s.parse_sidc(code)["symbol_set"] == "25"  # control measure, not land unit (10)
    assert s.is_valid_sidc(code)


@pytest.mark.parametrize(
    "bad, reason",
    [
        ("123", "20 digits"),
        ("1003100000121100000X", "all digits"),
        ("99031000001211000000", "version"),
        ("10039900001211000000", "symbol set"),
        ("10071000001211000000", "identity"),  # affiliation 7 is not valid
        ("10031090001211000000", "status"),  # status digit (pos 7) 9 is not valid
    ],
)
def test_validator_rejects_malformed(bad, reason):
    errors = s.validate_sidc(bad)
    assert errors and any(reason in e for e in errors), errors
    assert not s.is_valid_sidc(bad)


def test_parse_round_trips_the_structured_fields():
    code = s.sidc(
        "dismounted_infantry", s.StandardIdentity.NEUTRAL, status=s.Status.PRESENT_DAMAGED
    )
    f = s.parse_sidc(code)
    assert f["version"] == "10" and f["identity"] == "4" and f["status"] == "3"
    assert f["symbol_set"] == "10" and f["entity"] == "121100"


def test_message_carries_the_structured_tactical_data():
    """Unit id, position, affiliation, unit type, status, timestamp, direction+speed."""
    m = JdssMessage(
        header=MessageHeader(originator_id="node-b"),
        body=ContactSighting(
            location=Location(lat=50.8, lon=4.3),
            identity=s.StandardIdentity.HOSTILE,
            status=s.Status.PRESENT,
            description="dismounted patrol",
            course_deg=135.0,
            speed_mps=2.5,
        ),
    )
    assert m.header.originator_id == "node-b"  # unit identifier
    assert m.header.reporting_time is not None  # timestamp
    assert (m.body.location.lat, m.body.location.lon) == (50.8, 4.3)  # position
    assert int(m.body.identity) == int(s.StandardIdentity.HOSTILE)  # affiliation
    assert s.parse_sidc(m.body.sidc)["entity"] != ""  # unit type (entity)
    assert m.body.course_deg == 135.0 and m.body.speed_mps == 2.5  # direction + speed
    assert s.is_valid_sidc(m.body.sidc)  # renders as a valid APP-6(D)/2525D symbol


def test_all_sidcs_valid_in_reference_milsymbol_engine(tmp_path):
    """Cross-check every entity/affiliation/status SIDC against milsymbol — the reference
    MIL-STD-2525D / APP-6D renderer. Skipped when node/milsymbol aren't installed."""
    import json
    import pathlib
    import shutil
    import subprocess

    node = shutil.which("node")
    web = pathlib.Path(__file__).resolve().parents[1] / "web-ui"
    if node is None or not (web / "node_modules" / "milsymbol").exists():
        pytest.skip("node / milsymbol not available")

    codes = sorted(
        {
            s.sidc(ent, ident, status=st)
            for ent in s.ENTITIES
            for ident in s.StandardIdentity
            for st in s.Status
        }
    )
    codes_file = tmp_path / "codes.json"
    codes_file.write_text(json.dumps(codes))
    script = (
        "const ms=require('milsymbol');const fs=require('fs');"
        "const c=JSON.parse(fs.readFileSync(process.argv[1]));"
        "console.log(JSON.stringify(c.filter(x=>!new ms.Symbol(x).isValid())));"
    )
    out = subprocess.run(
        [node, "-e", script, str(codes_file)],
        cwd=str(web), capture_output=True, text=True, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    invalid = json.loads(out.stdout.strip().splitlines()[-1])
    assert invalid == [], f"non-conformant per milsymbol: {invalid}"


@pytest.mark.parametrize("course, ok", [(0, True), (360, True), (-1, False), (361, False)])
def test_course_bounds(course, ok):
    from pydantic import ValidationError

    if ok:
        Presence(location=Location(lat=1, lon=2), callsign="A", course_deg=course)
    else:
        with pytest.raises(ValidationError):
            Presence(location=Location(lat=1, lon=2), callsign="A", course_deg=course)
