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


class PairBlockPolicy:
    """Per-pair (observer, originator) blocks — the interactive N×N communication matrix.

    Holds the whole coalition pair-block map (so the authority can distribute it), but enforces
    only *this* node's row: it drops a message when the pair ``(this node, originator)`` is
    blocked. So the authority can say "node B must not accept from node C" and only node B
    enforces it — true per-pair control on top of the column/row policies."""

    name = "pairs"

    def __init__(self, node_id: str = "", pairs: dict[str, list[str]] | None = None) -> None:
        self.node_id = node_id
        self._pairs: dict[str, set[str]] = {o: set(v) for o, v in (pairs or {}).items()}

    def allows(self, message: JdssMessage) -> bool:
        blocked = self._pairs.get(self.node_id)
        return not blocked or message.header.originator_id not in blocked

    def block(self, observer: str, originator: str) -> None:
        self._pairs.setdefault(observer, set()).add(originator)

    def allow(self, observer: str, originator: str) -> None:
        peers = self._pairs.get(observer)
        if peers:
            peers.discard(originator)
            if not peers:
                self._pairs.pop(observer, None)

    def replace(self, pairs: dict[str, list[str]]) -> None:
        """Overwrite the whole pair map (used when a distributed coalition policy arrives)."""
        self._pairs = {o: set(v) for o, v in pairs.items() if v}

    def snapshot(self) -> dict[str, list[str]]:
        return {o: sorted(v) for o, v in self._pairs.items() if v}


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
