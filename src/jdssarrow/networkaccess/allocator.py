"""Network Access address allocation (Vol V).

Vol V governs how unicast and multicast IP addresses are defined and distributed to the
Layer-2/Layer-3 loaned radios *before* a coalition mission. This allocator provides
deterministic, collision-free assignment:

* **Unicast** — a stable per-node host address inside a mission subnet, derived from a hash
  of the node id so every gateway computes the same address for a peer without coordination.
* **Multicast** — a per-network group in the local admin-scoped range (239.x) plus a UDP
  port, so each coalition network maps to exactly one multicast group.

Deterministic derivation is what lets independently-configured national systems converge on
the same addressing plan from a shared mission id — the pre-mission distribution model.
"""

from __future__ import annotations

import hashlib


class DefaultAddressAllocator:
    name = "default"

    def __init__(
        self,
        mission_subnet: str = "10.77.0.0/16",
        multicast_base: str = "239.4.0.0",
        base_port: int = 46000,
    ) -> None:
        self._subnet_prefix = mission_subnet.split(".")[0:2]  # e.g. ["10", "77"]
        self._mcast_prefix = multicast_base.split(".")[0:2]  # e.g. ["239", "4"]
        self._base_port = base_port

    @staticmethod
    def _hash16(value: str) -> int:
        return int.from_bytes(hashlib.sha256(value.encode()).digest()[:2], "big")

    def allocate_unicast(self, node_id: str) -> str:
        h = self._hash16(node_id)
        third = (h >> 8) & 0xFF
        # avoid .0 (network) and .255 (broadcast) in the last octet
        fourth = (h & 0xFF) or 1
        if fourth == 255:
            fourth = 254
        return f"{self._subnet_prefix[0]}.{self._subnet_prefix[1]}.{third}.{fourth}"

    def multicast_group(self, network_id: str) -> tuple[str, int]:
        h = self._hash16(network_id)
        third = (h >> 8) & 0xFF
        fourth = h & 0xFF
        group = f"{self._mcast_prefix[0]}.{self._mcast_prefix[1]}.{third}.{fourth}"
        port = self._base_port + (h % 1000)
        return group, port
