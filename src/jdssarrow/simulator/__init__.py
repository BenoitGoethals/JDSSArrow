"""JDSS interoperability simulator.

Spawns a roster of JDSS-compliant *clients* — each a distinct soldier-system role driving a
real :class:`~jdssarrow.gateway.gateway.JdssGateway` — onto one coalition network and lets
them exchange messages exactly as AEP-76 prescribes. Because every client publishes through
the standard gateway → codec → security → IEM stack, compliance is structural: a client
cannot emit a non-conformant message.
"""

from jdssarrow.simulator.manager import SimulationManager
from jdssarrow.simulator.profiles import CLIENT_PROFILES, ClientProfile
from jdssarrow.simulator.rogue import ROGUE_MODES, RogueClient
from jdssarrow.simulator.scenario import DEFAULT_ROSTER, SimReport, Simulation

__all__ = [
    "CLIENT_PROFILES",
    "DEFAULT_ROSTER",
    "ROGUE_MODES",
    "ClientProfile",
    "RogueClient",
    "SimReport",
    "Simulation",
    "SimulationManager",
]
