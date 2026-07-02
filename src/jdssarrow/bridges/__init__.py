"""Protocol bridges — connect non-JDSS clients (e.g. ATAK/CoT) to a JDSS network."""

from jdssarrow.bridges.atak import AtakBridge

__all__ = ["AtakBridge"]
