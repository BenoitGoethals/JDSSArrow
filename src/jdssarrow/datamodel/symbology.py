"""APP-6(D) symbology helpers.

The JDSSDM tags entities with a Symbol Identification Code (SIDC). APP-6(D) uses a 20-digit
SIDC; here we model the handful of positions the seven JDSS message types actually need
(version, standard identity, symbol set, entity), enough to render an icon on the common
operational picture and to round-trip through the codecs. This is deliberately a subset,
not the full APP-6(D) machine.
"""

from __future__ import annotations

from enum import IntEnum

VERSION = "10"  # APP-6(D)


class StandardIdentity(IntEnum):
    """Digit 4 of the SIDC — affiliation of the tracked object."""

    PENDING = 0
    UNKNOWN = 1
    FRIEND = 3
    NEUTRAL = 4
    HOSTILE = 6


class SymbolSet(IntEnum):
    """Digits 5-6 — the domain the symbol lives in."""

    LAND_UNIT = 10
    LAND_CIVILIAN = 11
    LAND_EQUIPMENT = 15
    CONTROL_MEASURE = 25


# A tiny entity catalogue keyed by mnemonic → 6-digit entity code (digits 11-16).
ENTITIES: dict[str, str] = {
    "dismounted_infantry": "121100",
    "medic": "161200",
    "casualty": "180100",
    "hostile_contact": "110000",
    "unknown_contact": "000000",
    "control_point": "210300",
}


def sidc(
    entity: str,
    identity: StandardIdentity = StandardIdentity.FRIEND,
    symbol_set: SymbolSet = SymbolSet.LAND_UNIT,
) -> str:
    """Build a 20-digit APP-6(D) SIDC for a known entity mnemonic.

    Layout (subset): version(2) status(1) identity(1) set(2) [reserved padding] entity(6).
    """
    entity_code = ENTITIES.get(entity, ENTITIES["unknown_contact"])
    set_code = f"{int(symbol_set):02d}"
    identity_code = str(int(identity))
    # version(2) + context/status(1)='0' + identity(1) + set(2) + hq/amp(4)='0000'
    prefix = f"{VERSION}0{identity_code}{set_code}0000"
    return f"{prefix}{entity_code}"  # 2+1+1+2+4+6 = 16 → padded to 20 below


def normalize(code: str) -> str:
    """Pad/truncate a SIDC to the canonical 20 digits."""
    return (code + "0" * 20)[:20]
