"""APP-6(D) / MIL-STD-2525D symbology helpers.

The JDSSDM tags entities with a 20-digit Symbol Identification Code (SIDC). The two standards
share the same digit-based SIDC, so one code renders on any conforming APP-6(D) or MIL-STD-2525D
symbol engine. The receiving client builds the icon from the *structured attributes* the SIDC
encodes — standard identity (affiliation), symbol set (domain), status, and entity (unit type) —
exactly the "render the symbol from the attributes" model.

Digit layout of the 20-digit SIDC (2525D / APP-6D "Version 10", Set A):

    1-2  version        (``10``)
    3    context        0=reality 1=exercise 2=simulation
    4    standard identity (affiliation) — see :class:`StandardIdentity`
    5-6  symbol set     (domain) — see :class:`SymbolSet`
    7    status         0=present 1=planned/anticipated 3=present/damaged 4=present/destroyed
    8    HQ / task force / dummy
    9-10 amplifier / descriptor
    11-16 entity        (unit type: entity + type + subtype)
    17-18 sector-1 modifier
    19-20 sector-2 modifier
"""

from __future__ import annotations

from enum import IntEnum

VERSION = "10"  # APP-6(D) / MIL-STD-2525D "Version 10"


class StandardIdentity(IntEnum):
    """Digit 4 of the SIDC — affiliation of the tracked object."""

    PENDING = 0
    UNKNOWN = 1
    ASSUMED_FRIEND = 2
    FRIEND = 3
    NEUTRAL = 4
    SUSPECT = 5
    HOSTILE = 6


class Status(IntEnum):
    """Digit 7 of the SIDC — operational status / condition."""

    PRESENT = 0
    ANTICIPATED = 1  # planned / anticipated / suspect
    PRESENT_DAMAGED = 3
    PRESENT_DESTROYED = 4


class SymbolSet(IntEnum):
    """Digits 5-6 — the domain the symbol lives in."""

    LAND_UNIT = 10
    LAND_CIVILIAN = 11
    LAND_EQUIPMENT = 15
    CONTROL_MEASURE = 25


#: the symbol-set codes we recognise as valid 2525D / APP-6D domains (Set A).
VALID_SYMBOL_SETS: frozenset[str] = frozenset(
    {"00", "01", "02", "05", "06", "10", "11", "15", "20", "25", "27",
     "30", "35", "36", "40", "45", "46", "47", "50", "51", "52", "53", "54", "60"}
)


# Entity catalogue keyed by mnemonic → (symbol set, 6-digit entity code). Each entity lives in a
# specific 2525D symbol set, so the SIDC is only valid when the entity is placed in *its* set —
# e.g. a control-measure point belongs to CONTROL_MEASURE (25), not LAND_UNIT (10).
ENTITIES: dict[str, tuple[SymbolSet, str]] = {
    "dismounted_infantry": (SymbolSet.LAND_UNIT, "121100"),
    "medic": (SymbolSet.LAND_UNIT, "161200"),
    "casualty": (SymbolSet.LAND_UNIT, "180100"),
    "hostile_contact": (SymbolSet.LAND_UNIT, "110000"),
    "unknown_contact": (SymbolSet.LAND_UNIT, "000000"),
    "control_point": (SymbolSet.CONTROL_MEASURE, "210300"),
}


def sidc(
    entity: str,
    identity: StandardIdentity = StandardIdentity.FRIEND,
    symbol_set: SymbolSet | None = None,
    status: Status = Status.PRESENT,
) -> str:
    """Build the canonical 20-digit APP-6(D)/2525D SIDC for a known entity mnemonic.

    The entity's own symbol set is used unless ``symbol_set`` is given. Renders directly on any
    conforming 2525D / APP-6D symbol engine (see module docstring for the digit layout).
    """
    entity_set, entity_code = ENTITIES.get(entity, ENTITIES["unknown_contact"])
    set_code = f"{int(symbol_set if symbol_set is not None else entity_set):02d}"
    # version(2) context(1)='0' identity(1) set(2) status(1) hqtf(1)='0' amplifier(2)='00'
    prefix = f"{VERSION}0{int(identity)}{set_code}{int(status)}000"
    return normalize(f"{prefix}{entity_code}")  # + entity(6) + modifiers(4) → 20 digits


def with_identity(code: str, identity: StandardIdentity) -> str:
    """Return ``code`` with its affiliation digit (position 4) set to ``identity``.

    Keeps a message's SIDC consistent with its ``identity`` attribute, so the symbol a client
    renders always matches the reported affiliation."""
    code = normalize(code)
    return code[:3] + str(int(identity)) + code[4:]


def normalize(code: str) -> str:
    """Pad/truncate a SIDC to the canonical 20 digits."""
    return (code + "0" * 20)[:20]


def parse_sidc(code: str) -> dict[str, str]:
    """Decode a 20-digit SIDC into its structured fields (the attributes a client renders from)."""
    c = normalize(code)
    return {
        "version": c[0:2],
        "context": c[2],
        "identity": c[3],
        "symbol_set": c[4:6],
        "status": c[6],
        "hq_tf_dummy": c[7],
        "amplifier": c[8:10],
        "entity": c[10:16],
        "modifier_1": c[16:18],
        "modifier_2": c[18:20],
    }


def validate_sidc(code: str) -> list[str]:
    """Conformance-check a SIDC against APP-6(D) / MIL-STD-2525D. Empty list == valid.

    Verifies the length, that every position is a digit, and that the standards-constrained
    fields (version, context, standard identity, symbol set, status) hold legal values."""
    errors: list[str] = []
    if len(code) != 20:
        errors.append(f"SIDC must be 20 digits, got {len(code)}")
        return errors
    if not code.isdigit():
        errors.append("SIDC must be all digits")
        return errors
    f = parse_sidc(code)
    if f["version"] != VERSION:
        errors.append(f"version must be {VERSION} (2525D/APP-6D), got {f['version']}")
    if f["context"] not in {"0", "1", "2"}:
        errors.append(f"context must be 0=reality/1=exercise/2=simulation, got {f['context']}")
    if int(f["identity"]) not in {int(i) for i in StandardIdentity}:
        errors.append(f"standard identity {f['identity']} is not a valid affiliation (0-6)")
    if f["symbol_set"] not in VALID_SYMBOL_SETS:
        errors.append(f"symbol set {f['symbol_set']} is not a valid 2525D domain")
    if f["status"] not in {"0", "1", "2", "3", "4", "5"}:
        errors.append(f"status {f['status']} is not a valid operational status (0-5)")
    return errors


def is_valid_sidc(code: str) -> bool:
    return not validate_sidc(code)
