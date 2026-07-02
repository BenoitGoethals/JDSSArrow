"""JDSSArrow — a reference implementation of the Joint Dismounted Soldier System.

STANAG 4677 / AEP-76 (5 volumes). See :mod:`jdssarrow.interfaces` for the extension
points and :mod:`jdssarrow.gateway` for the composition root.
"""

__version__ = "0.1.0"

# AEP-76 volume map, exposed for introspection / the web UI.
AEP76_VOLUMES: dict[str, str] = {
    "I": "Security",
    "II": "Data Model (JDSSDM)",
    "III": "Loaned Radio",
    "IV": "Information Exchange Mechanism (JDSSIEM)",
    "V": "Network Access",
}
