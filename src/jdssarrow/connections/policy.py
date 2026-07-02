"""Connection-management policies.

A policy is one node's *row* of the coalition connection matrix: for every potential peer it
answers "do I accept traffic from you?". The gateway enforces it on ingest, so managing the
matrix (blocking/allowing a peer) immediately changes what the network delivers.

* :class:`AllowAllPolicy` — accept everyone (open exercise).
* :class:`MatrixConnectionPolicy` — a default action plus per-peer overrides, editable at
  runtime. This is the manageable matrix row.

Policies are pluggable (``jdssarrow.policies`` entry-point group).
"""

from __future__ import annotations

from jdssarrow.datamodel.messages import JdssMessage


class AllowAllPolicy:
    name = "allow_all"

    def allows(self, message: JdssMessage) -> bool:
        return True

    def snapshot(self) -> dict:
        return {"policy": self.name, "default_action": "allow", "overrides": {}}


class CompositePolicy:
    """Accept a message only if *every* sub-policy accepts it.

    Used to layer a distributed coalition-wide policy on top of a node's own local policy: a
    peer must be allowed by both the coalition and the local node to be delivered.
    """

    name = "composite"

    def __init__(self, *policies: object) -> None:
        self._policies = policies

    def allows(self, message: JdssMessage) -> bool:
        return all(p.allows(message) for p in self._policies)  # type: ignore[attr-defined]


class MatrixConnectionPolicy:
    """Default allow/deny plus explicit per-peer overrides (a manageable matrix row)."""

    name = "matrix"

    def __init__(
        self,
        node_id: str = "",
        default_action: str = "allow",
        overrides: dict[str, bool] | None = None,
    ) -> None:
        self.node_id = node_id
        self.default_allow = default_action != "deny"
        # peer_id -> True(allow) / False(block)
        self._overrides: dict[str, bool] = dict(overrides or {})

    # ---------------------------------------------------------------- enforcement
    def allows_peer(self, peer_id: str) -> bool:
        return self._overrides.get(peer_id, self.default_allow)

    def allows(self, message: JdssMessage) -> bool:
        return self.allows_peer(message.header.originator_id)

    # ---------------------------------------------------------------- management
    def block(self, peer_id: str) -> None:
        self._overrides[peer_id] = False

    def allow(self, peer_id: str) -> None:
        self._overrides[peer_id] = True

    def reset(self, peer_id: str) -> None:
        """Remove an override so the peer falls back to the default action."""
        self._overrides.pop(peer_id, None)

    def set_default(self, action: str) -> None:
        self.default_allow = action != "deny"

    def replace(self, default_action: str, overrides: dict[str, str]) -> None:
        """Overwrite the whole policy (used when a distributed coalition policy arrives)."""
        self.default_allow = default_action != "deny"
        self._overrides = {p: (v == "allow") for p, v in overrides.items()}

    def blocked_peers(self) -> list[str]:
        return sorted(p for p, allowed in self._overrides.items() if not allowed)

    def snapshot(self) -> dict:
        return {
            "policy": self.name,
            "node_id": self.node_id,
            "default_action": "allow" if self.default_allow else "deny",
            "overrides": {
                p: ("allow" if allowed else "block") for p, allowed in self._overrides.items()
            },
        }
