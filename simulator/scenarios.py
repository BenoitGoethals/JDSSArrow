"""Tactical scenarios for the external JDSS simulator.

Two historically flavoured vignettes (coordinates approximate) that drive coalition units around
looping routes, each emitting the JDSS message set:

* **eben_emael** — the 10 May 1940 glider/airborne assault on Fort Eben-Emael and the Albert
  Canal bridges (Vroenhoven, Veldwezelt, Kanne).
* **narvik** — the April-May 1940 Allied amphibious landings around Narvik (Bjerkvik, Ankenes,
  Øyjord) supported by naval gunfire in the Ofotfjord.

Routes are closed loops, so units patrol them repeatedly while the simulation runs.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from simulator.geo import Point


@dataclass(frozen=True)
class UnitSpec:
    node_id: str
    callsign: str
    nation: str
    role: str
    entity: str  # symbology entity mnemonic (jdssarrow.datamodel.symbology.ENTITIES)
    route: list[Point]  # closed patrol loop of (lat, lon)
    speed_mps: float
    behaviors: frozenset[str]  # subset of {presence, contact, casevac, chat, overlay}
    unit: str = ""


@dataclass(frozen=True)
class Enemy:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class Scenario:
    key: str
    name: str
    description: str
    network_id: str
    center: Point
    units: list[UnitSpec]
    enemies: list[Enemy]
    orders: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- Eben-Emael
_EBEN = Scenario(
    key="eben_emael",
    name="Airborne assault on Fort Eben-Emael",
    description="Glider-borne assault on the fort and the Albert Canal bridges, 10 May 1940.",
    network_id="exercise-granit",
    center=(50.7975, 5.6792),
    units=[
        UnitSpec("granit-1", "GRANIT-1", "DEU", "assault_pioneer", "dismounted_infantry",
                 [(50.7968, 5.6790), (50.7980, 5.6805), (50.7992, 5.6788), (50.7979, 5.6772)],
                 2.0, frozenset({"presence", "contact", "casevac"}), "Sturmgruppe Granit"),
        UnitSpec("granit-2", "GRANIT-2", "DEU", "engineer", "dismounted_infantry",
                 [(50.7975, 5.6792), (50.7985, 5.6810), (50.7998, 5.6795), (50.7986, 5.6778)],
                 1.6, frozenset({"presence", "contact"}), "Sturmgruppe Granit"),
        UnitSpec("granit-cp", "GRANIT-CP", "DEU", "commandpost", "control_point",
                 [(50.7962, 5.6800), (50.7966, 5.6812), (50.7958, 5.6812), (50.7955, 5.6799)],
                 0.8, frozenset({"presence", "chat", "overlay"}), "Sturmgruppe Granit"),
        UnitSpec("granit-med", "GRANIT-MED", "DEU", "medic", "medic",
                 [(50.7970, 5.6795), (50.7978, 5.6807), (50.7969, 5.6810), (50.7963, 5.6797)],
                 1.4, frozenset({"presence", "casevac"}), "Sturmgruppe Granit"),
        UnitSpec("beton-1", "BETON-1", "DEU", "assault_pioneer", "dismounted_infantry",
                 [(50.8419, 5.6389), (50.8431, 5.6402), (50.8440, 5.6380), (50.8425, 5.6370)],
                 2.4, frozenset({"presence", "contact"}), "Sturmgruppe Beton (Vroenhoven)"),
        UnitSpec("stahl-1", "STAHL-1", "DEU", "assault_pioneer", "dismounted_infantry",
                 [(50.8647, 5.6169), (50.8660, 5.6185), (50.8672, 5.6160), (50.8655, 5.6150)],
                 2.4, frozenset({"presence", "contact", "casevac"}),
                 "Sturmgruppe Stahl (Veldwezelt)"),
        UnitSpec("eisen-1", "EISEN-1", "DEU", "assault_pioneer", "dismounted_infantry",
                 [(50.7836, 5.6803), (50.7848, 5.6818), (50.7858, 5.6795), (50.7842, 5.6786)],
                 2.2, frozenset({"presence", "contact"}), "Sturmgruppe Eisen (Kanne)"),
    ],
    enemies=[
        Enemy("Coupole Nord (Bloc B I)", 50.8005, 5.6810),
        Enemy("Mi-Nord cupola", 50.7995, 5.6775),
        Enemy("Bloc II casemate", 50.7952, 5.6802),
        Enemy("Maastricht 1 cupola", 50.7988, 5.6760),
        Enemy("Canal AA position", 50.8420, 5.6402),
    ],
    orders=[
        "GRANIT: neutralise cupoles 12, 18 and 23 — charges on the domes.",
        "BETON: seize Vroenhoven bridge intact, hold the west bank.",
        "STAHL: Veldwezelt bridge secured, expect counter-attack from Lanaken.",
        "All Sturmgruppen: consolidate, await 51. Pionier-Bataillon relief.",
    ],
)

# --------------------------------------------------------------------------- Narvik
_NARVIK = Scenario(
    key="narvik",
    name="Beach landing at Narvik",
    description="Allied amphibious landings around Narvik with naval gunfire support, 1940.",
    network_id="exercise-narvik",
    center=(68.4385, 17.4272),
    units=[
        UnitSpec("naval-1", "WARSPITE", "GBR", "fire_support", "control_point",
                 [(68.4400, 17.2400), (68.4450, 17.2900), (68.4380, 17.3300), (68.4360, 17.2700)],
                 6.0, frozenset({"presence", "overlay"}), "Naval gunfire support (Ofotfjord)"),
        UnitSpec("alpin-1", "ALPIN-1", "FRA", "mountain_infantry", "dismounted_infantry",
                 [(68.5300, 17.5500), (68.5100, 17.5200), (68.4800, 17.4800), (68.5050, 17.5250)],
                 1.8, frozenset({"presence", "contact", "casevac"}), "13e DBLE (Bjerkvik)"),
        UnitSpec("alpin-2", "ALPIN-2", "FRA", "mountain_infantry", "dismounted_infantry",
                 [(68.4200, 17.3800), (68.4300, 17.4100), (68.4380, 17.4270), (68.4250, 17.3900)],
                 1.8, frozenset({"presence", "contact"}), "Chasseurs Alpins (Ankenes)"),
        UnitSpec("polska-1", "POLSKA-1", "POL", "mountain_infantry", "dismounted_infantry",
                 [(68.4000, 17.5000), (68.4150, 17.4700), (68.4300, 17.4400), (68.4100, 17.4850)],
                 1.7, frozenset({"presence", "contact", "casevac"}), "Polish Highland Brigade"),
        UnitSpec("nor-1", "NORGE-1", "NOR", "mountain_infantry", "dismounted_infantry",
                 [(68.4700, 17.4700), (68.4600, 17.4500), (68.4500, 17.4400), (68.4650, 17.4650)],
                 1.6, frozenset({"presence", "contact"}), "6. Divisjon (Øyjord)"),
        UnitSpec("med-1", "NARVIK-MED", "GBR", "medic", "medic",
                 [(68.4300, 17.4000), (68.4350, 17.4150), (68.4280, 17.4200), (68.4260, 17.4050)],
                 1.2, frozenset({"presence", "casevac"}), "Field ambulance"),
        UnitSpec("hq-1", "NARVIK-HQ", "GBR", "commandpost", "control_point",
                 [(68.4380, 17.3900), (68.4400, 17.4000), (68.4360, 17.4020), (68.4350, 17.3920)],
                 0.6, frozenset({"presence", "chat", "overlay"}), "24th Guards Bde HQ"),
    ],
    enemies=[
        Enemy("Gebirgsjäger, Narvik town", 68.4385, 17.4272),
        Enemy("Mountain gun, Framnes", 68.4330, 17.4450),
        Enemy("MG nest, Ankenes ridge", 68.4180, 17.3850),
        Enemy("Gebirgsjäger, Beisfjord", 68.4050, 17.5000),
        Enemy("Naval landing party, harbour", 68.4360, 17.4300),
    ],
    orders=[
        "ALPIN: land Bjerkvik, clear the beach, advance on Øyjord.",
        "POLSKA: press from Ankenes toward Beisfjord, screen the southern flank.",
        "WARSPITE: suppress the harbour batteries on call, danger-close to friendly troops.",
        "All callsigns: converge on Narvik, report enemy mountain positions.",
    ],
)

SCENARIOS: dict[str, Scenario] = {_EBEN.key: _EBEN, _NARVIK.key: _NARVIK}

#: default cohort size for the stress test.
STRESS_OPERATORS = 500


def expand_scenario(scenario: Scenario, count: int, *, seed: int = 4677) -> Scenario:
    """Clone a scenario up to ``count`` synthetic operators for load/stress testing.

    Operators are scattered across the scenario's area of operations (a ~5 km box around its
    centre) and each gets its own small closed patrol loop, so 500 tracks move independently.
    The base units' roles, nations, entities and behaviours are cycled round-robin, so the
    generated traffic keeps a realistic message mix (presence, contacts, casevac, chat, overlay).
    """
    base = scenario.units
    if not base or count <= len(base):
        return scenario
    rng = random.Random(seed)
    clat, clon = scenario.center
    # ~0.045° lat ≈ 5 km; widen lon so the box is roughly square at these latitudes
    span_lat = 0.045
    span_lon = span_lat / max(0.2, math.cos(math.radians(clat)))
    units: list[UnitSpec] = []
    for i in range(count):
        proto = base[i % len(base)]
        lat = clat + rng.uniform(-span_lat, span_lat)
        lon = clon + rng.uniform(-span_lon, span_lon)
        units.append(
            UnitSpec(
                node_id=f"stress-{i + 1:04d}",
                callsign=f"OP-{i + 1:04d}",
                nation=proto.nation,
                role=proto.role,
                entity=proto.entity,
                route=_patrol_loop(lat, lon, rng),
                speed_mps=proto.speed_mps,
                behaviors=proto.behaviors,
                unit=f"Stress cohort {i // len(base) + 1}",
            )
        )
    return Scenario(
        key=f"{scenario.key}__stress{count}",
        name=f"{scenario.name} — STRESS {count} operators",
        description=f"Load test: {count} synthetic operators derived from {scenario.name}.",
        network_id=scenario.network_id,
        center=scenario.center,
        units=units,
        enemies=scenario.enemies,
        orders=scenario.orders,
    )


def _patrol_loop(lat: float, lon: float, rng: random.Random) -> list[Point]:
    """A small jittered closed quadrilateral (~200-500 m per leg) around a start point."""
    d = rng.uniform(0.002, 0.005)
    return [
        (lat, lon),
        (lat + d, lon + d * 0.6),
        (lat + d * 0.4, lon - d),
        (lat - d, lon + d * 0.3),
    ]
