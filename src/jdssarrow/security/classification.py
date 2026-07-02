"""Security classification / caveat tagging (Vol I).

A small, self-contained helper for stamping messages with a classification level and
releasability caveat. The web monitor uses this to colour-code the operational picture and
to refuse to display data above the operator's clearance.
"""

from __future__ import annotations

from enum import IntEnum


class Classification(IntEnum):
    UNCLASSIFIED = 0
    RESTRICTED = 1
    CONFIDENTIAL = 2
    SECRET = 3

    @property
    def label(self) -> str:
        return {
            0: "NATO UNCLASSIFIED",
            1: "NATO RESTRICTED",
            2: "NATO CONFIDENTIAL",
            3: "NATO SECRET",
        }[int(self)]


def releasable_to(caveat: str, nation: str) -> bool:
    """True if ``nation`` (alpha-3) is on a ``REL TO`` caveat like ``"USA GBR DEU"``."""
    if not caveat or caveat.upper() in {"ALL", "REL ALL"}:
        return True
    return nation.upper() in {tok.upper() for tok in caveat.replace(",", " ").split()}
